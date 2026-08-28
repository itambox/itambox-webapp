"""Issue-#445 guarded django-q task resubmission.

The django-q Success/Failure proxy admins ship a resubmission action that
blindly re-enqueues the stored task path. Once the task paths have been cut
over, a historical row referencing a predecessor path (or a noncanonical
moved alias) must never be re-enqueued — the path no longer resolves on the
cutover branch, and silently re-enqueueing it would create queue debt instead
of retrying work. ``GuardedTaskAdmin``/``GuardedFailAdmin`` replace the vendor
admins via ``core.apps.CoreConfig.ready()``. Validation, ORM-broker publication
and Failure-row deletion are all-or-nothing: every selected row is validated
before publication, and all queue inserts plus deletion share one database
transaction. Unsupported/synchronous brokers fail closed because they cannot
provide that transaction boundary.

Kept in its own module so imports of django-q models can never be drawn into
an admin default-site resolution cycle.
"""

import json

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_q.admin import FailAdmin, TaskAdmin
from django_q.conf import Conf
from django_q.models import Failure
from django_q.tasks import async_task

LEGACY_TASK_PATHS = frozenset(
    {
        "core.tasks.evaluate_alert_rules_task",
        "core.tasks.run_alert_rule_now",
        "core.tasks.generate_scheduled_report_task",
        "core.tasks.send_webhook_task",
        "assets.tasks.notify_new_request_task",
        "core.tasks.bulk_checkin_task",
        "core.tasks.bulk_checkout_task",
        "core.tasks.calculate_depreciation",
        "core.tasks.bulk_dispose_task",
        "core.tasks.sync_tenant_intune",
        "core.tasks.labels.generate_label_batch_task",
        "core.tasks.labels.generate_label_pdf_batch_task",
    }
)
CANONICAL_TASK_PATHS = frozenset(
    {
        "extras.tasks.alerts.evaluate_alert_rules_task",
        "extras.tasks.alerts.run_alert_rule_now",
        "extras.tasks.reports.generate_scheduled_report_task",
        "extras.tasks.webhooks.send_webhook_task",
        "extras.tasks.webhooks.recover_pending_webhook_deliveries",
        "assets.tasks.requests.notify_new_request_task",
        "assets.tasks.checkin.bulk_checkin_task",
        "assets.tasks.checkout.bulk_checkout_task",
        "assets.tasks.depreciation.calculate_depreciation",
        "assets.tasks.disposal.bulk_dispose_task",
        "assets.tasks.intune_sync.sync_tenant_intune",
        "assets.tasks.labels.generate_label_batch_task",
        "assets.tasks.labels.generate_label_pdf_batch_task",
    }
)
MOVED_NEIGHBORHOOD_PREFIXES = (
    "core.tasks.alerts.",
    "core.tasks.reports.",
    "core.tasks.webhooks.",
    "core.tasks.checkin.",
    "core.tasks.checkout.",
    "core.tasks.depreciation.",
    "core.tasks.disposal.",
    "core.tasks.intune_sync.",
    "core.tasks.labels.",
    "assets.tasks.requests.",
    "assets.tasks.alerts.",
    "assets.tasks.reports.",
    "assets.tasks.webhooks.",
    "assets.tasks.checkin.",
    "assets.tasks.checkout.",
    "assets.tasks.depreciation.",
    "assets.tasks.disposal.",
    "assets.tasks.intune_sync.",
    "assets.tasks.labels.",
)
MOVED_NEIGHBORHOOD_MODULES = (
    "core.tasks.checkin",
    "core.tasks.checkout",
    "core.tasks.depreciation",
    "core.tasks.disposal",
    "core.tasks.intune_sync",
    "core.tasks.labels",
    "core.tasks.alerts",
    "core.tasks.reports",
    "core.tasks.webhooks",
    "assets.tasks.requests",
)

BLOCKED_RESUBMISSION_CODE = "task_resubmission.blocked_moved_path"
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


def is_blocked_task_path(func):
    """True when a stored task path is a predecessor or noncanonical alias."""
    if not isinstance(func, str) or not func:
        return False
    if func in CANONICAL_TASK_PATHS:
        return False
    if func in LEGACY_TASK_PATHS:
        return True
    if func in MOVED_NEIGHBORHOOD_MODULES:
        return True
    return any(func.startswith(prefix) for prefix in MOVED_NEIGHBORHOOD_PREFIXES)


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
    blocked = sorted({path for task in tasks for path in (task.func, task.hook) if is_blocked_task_path(path)})
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
