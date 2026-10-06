"""Guarded django-q task resubmission.

The django-q Success/Failure proxy admins ship a resubmission action that
blindly re-enqueues the stored task path. A historical row whose task function
or hook no longer resolves to a callable must never be re-enqueued: it would
create queue debt instead of retrying work. ``GuardedTaskAdmin``/``GuardedFailAdmin``
replace the vendor admins via ``core.apps.CoreConfig.ready()``. Validation,
ORM-broker publication and Failure-row deletion are all-or-nothing: every
selected row is validated before publication, and all queue inserts plus
deletion share one database transaction. Unsupported/synchronous brokers fail
closed because they cannot provide that transaction boundary.

Kept in its own module so imports of django-q models can never be drawn into
an admin default-site resolution cycle.
"""

import importlib
import json

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_q.admin import FailAdmin, TaskAdmin
from django_q.conf import Conf
from django_q.models import Failure
from django_q.tasks import async_task

BLOCKED_RESUBMISSION_CODE = "task_resubmission.unresolvable_path"
UNSUPPORTED_BROKER_CODE = "task_resubmission.unsupported_broker"
ENQUEUE_FAILED_CODE = "task_resubmission.enqueue_failed"
RESERVED_ASYNC_TASK_KWARGS = frozenset(
    {
        "ack_failure",
        "broker",
        "cached",
        "chain",
        "cluster",
        "group",
        "hook",
        "iter_cached",
        "iter_count",
        "q_options",
        "save",
        "sync",
        "task_name",
        "timeout",
    }
)


def is_unresolvable_task_path(func):
    """True when a stored task path no longer resolves to a callable."""
    if not isinstance(func, str) or not func:
        return False
    module_name, _sep, attribute = func.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ValueError):
        return True
    return not callable(getattr(module, attribute, None))


def _task_payload(task):
    """Normalize native q2 values and the guarded action's legacy JSON form."""
    try:
        if not isinstance(task.func, str) or not task.func:
            raise ValueError
        if task.hook is not None and (not isinstance(task.hook, str) or not task.hook):
            raise ValueError
        args = json.loads(task.args) if isinstance(task.args, str) and task.args else task.args or ()
        kwargs = json.loads(task.kwargs) if isinstance(task.kwargs, str) and task.kwargs else task.kwargs or {}
        if not isinstance(args, (list, tuple)) or not isinstance(kwargs, dict):
            raise ValueError
        if any(not isinstance(key, str) or key in RESERVED_ASYNC_TASK_KWARGS for key in kwargs):
            raise ValueError
        return args, kwargs
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def resubmit_task_guarded(model_admin, request, queryset):
    """Atomically validate and republish a selected historical task set."""
    tasks = list(queryset)
    blocked = sorted({path for task in tasks for path in (task.func, task.hook) if is_unresolvable_task_path(path)})
    payloads = [(task, _task_payload(task)) for task in tasks]
    invalid = [task for task, payload in payloads if payload is None]
    if blocked or invalid:
        model_admin.message_user(
            request,
            f"[{BLOCKED_RESUBMISSION_CODE}] blocked paths: {', '.join(blocked)}"
            + (f" | invalid payloads: {len(invalid)}" if invalid else ""),
            level="warning",
        )
        return
    if not payloads:
        return

    database_aliases = {task._state.db or "default" for task, _payload in payloads}
    if len(database_aliases) != 1 or not isinstance(Conf.ORM, str) or Conf.ORM not in database_aliases or Conf.SYNC:
        model_admin.message_user(request, f"[{UNSUPPORTED_BROKER_CODE}]", level="warning")
        return
    database_alias = next(iter(database_aliases))

    try:
        with transaction.atomic(using=database_alias):
            for task, payload in payloads:
                args, kwargs = payload
                async_task(
                    task.func,
                    *args,
                    hook=task.hook,
                    group=task.group,
                    cluster=task.cluster,
                    **kwargs,
                )
            if model_admin.model is Failure:
                Failure.objects.using(database_alias).filter(pk__in=[task.pk for task, _payload in payloads]).delete()
    # broad except: boundary-isolation: rollback ORM queue writes and report only a stable code
    except Exception:
        model_admin.message_user(request, f"[{ENQUEUE_FAILED_CODE}]", level="warning")


resubmit_task_guarded.short_description = _("Resubmit selected tasks to queue")


class GuardedTaskAdmin(TaskAdmin):
    """Success-task admin with the issue-#445 guarded resubmission action."""

    actions = [resubmit_task_guarded]


class GuardedFailAdmin(FailAdmin):
    """Failure-task admin with the issue-#445 guarded resubmission action."""

    actions = [resubmit_task_guarded]
