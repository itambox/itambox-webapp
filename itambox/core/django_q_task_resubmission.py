"""Issue-#445 guarded django-q task resubmission.

The django-q Success/Failure proxy admins ship a resubmission action that
blindly re-enqueues the stored task path. Once the task paths have been cut
over, a historical row referencing a predecessor path (or a noncanonical
moved alias) must never be re-enqueued — the path no longer resolves on the
cutover branch, and silently re-enqueueing it would create queue debt instead
of retrying work. ``GuardedTaskAdmin``/``GuardedFailAdmin`` replace the vendor
admins via ``core.apps.CoreConfig.ready()`` and are all-or-nothing: when ANY
selected row is blocked, NOTHING is enqueued and no Failure row is deleted.

Kept in its own module so imports of django-q models can never be drawn into
an admin default-site resolution cycle.
"""

import json

from django.utils.translation import gettext_lazy as _
from django_q.admin import FailAdmin, TaskAdmin
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


def is_blocked_task_path(func):
    """True when a stored task path is a predecessor or noncanonical alias."""
    if not isinstance(func, str) or not func:
        return False
    if func in LEGACY_TASK_PATHS:
        return True
    if func in MOVED_NEIGHBORHOOD_MODULES:
        return True
    return any(func.startswith(prefix) for prefix in MOVED_NEIGHBORHOOD_PREFIXES)


def _task_payload(task):
    """Parse django-q2's JSON-encoded args/kwargs; None when malformed."""
    try:
        args = json.loads(task.args) if isinstance(task.args, str) else (task.args or ())
        kwargs = json.loads(task.kwargs) if isinstance(task.kwargs, str) else (task.kwargs or {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError
        return args, kwargs
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def resubmit_task_guarded(model_admin, request, queryset):
    """Resubmit selected tasks only when every selected path is still live.

    All-or-nothing: when any selected row references a moved predecessor
    path or noncanonical alias, the whole selection is rejected before any
    enqueue or Failure deletion. The rejection message carries only the
    stable error code and the blocked path identities.
    """
    blocked = sorted({task.func for task in queryset if is_blocked_task_path(task.func)})
    invalid = [task for task in queryset if _task_payload(task) is None]
    if blocked or invalid:
        model_admin.message_user(
            request,
            f"[{BLOCKED_RESUBMISSION_CODE}] blocked paths: {', '.join(blocked)}"
            + (f" | invalid payloads: {len(invalid)}" if invalid else ""),
            level="warning",
        )
        return
    for task in queryset:
        args, kwargs = _task_payload(task)
        async_task(
            task.func,
            *args,
            hook=task.hook,
            group=task.group,
            cluster=task.cluster,
            **kwargs,
        )
        if model_admin.model is Failure:
            task.delete()


resubmit_task_guarded.short_description = _("Resubmit selected tasks to queue")


class GuardedTaskAdmin(TaskAdmin):
    """Success-task admin with the issue-#445 guarded resubmission action."""

    actions = [resubmit_task_guarded]


class GuardedFailAdmin(FailAdmin):
    """Failure-task admin with the issue-#445 guarded resubmission action."""

    actions = [resubmit_task_guarded]
