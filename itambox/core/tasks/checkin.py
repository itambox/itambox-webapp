"""Background task: scanner-driven bulk check-in of hardware assets.

Mirrors ``bulk_checkout_task`` — wraps the run in ``TaskContext`` so change-log
entries are attributed, locks each asset row with ``select_for_update``, and
delegates the per-asset state change to the canonical ``checkin_asset`` service
so single and bulk check-in stay behaviourally identical.
"""

import datetime
import logging
from collections.abc import Sequence

from django.db import transaction
from django.utils.translation import gettext as _

from core.models import Job, Notification

from .context import TaskContext
from core.tasks.utils import TaskResult, TaskStatus, classify_task_error, reverse_job_detail

logger = logging.getLogger(__name__)


def _parse_date(value: str | datetime.date | None) -> datetime.date | None:
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def bulk_checkin_task(
    job_id: int,
    asset_pks: Sequence[int | str],
    user_id: int | None,
    tenant_id: int | None = None,
    status_id: int | str | None = None,
    location_id: int | str | None = None,
    checkin_date: str | datetime.date | None = None,
    notes: str = "",
) -> TaskResult:
    """Asynchronously check in selected hardware assets.

    Assets with no active assignment (and no location) are a no-op in
    ``checkin_asset`` — they are counted as *skipped* rather than failed.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="assets.bulk_checkin") as ctx:
        log_extra = {**ctx.log_context, "job_id": job_id}
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Bulk check-in job not found", extra=log_extra)
                return TaskResult(TaskStatus.TERMINAL, "checkin.job_not_found")

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping check-in.", job_id)
                return TaskResult(TaskStatus.SKIPPED, "checkin.job_not_pending")
            job.append_log("Initializing asynchronous bulk check-in pipeline...")
            job.append_log(f"Assets to process: {len(asset_pks)}")

            try:
                from assets.models import Asset, StatusLabel
                from assets.services import checkin_asset
                from organization.models import Location

                status = StatusLabel.objects.filter(pk=status_id).first() if status_id else None
                location = Location.objects.filter(pk=location_id).first() if location_id else None
                resolved_date = _parse_date(checkin_date)

                success_count = 0
                skipped_count = 0
                failure_count = 0

                for pk in asset_pks:
                    try:
                        with transaction.atomic():
                            asset = Asset.objects.select_for_update().get(pk=pk)
                            # location=None → checkin_asset() preserves the asset's current location.
                            result = checkin_asset(
                                asset,
                                user=ctx.user,
                                status=status,
                                location=location,
                                checkin_date=resolved_date,
                                notes=notes,
                            )
                        if result is None:
                            skipped_count += 1
                            job.append_log(f" - Asset PK {pk} skipped (not checked out).")
                        else:
                            success_count += 1
                            job.append_log(f" - Asset PK {pk} checked in successfully.")
                    # broad except: boundary-isolation: one asset failure must not abort the requested batch
                    except Exception as ex:
                        failure_count += 1
                        job.append_log(f" - Asset PK {pk} failed [checkin.item_failed].")
                        logger.warning(
                            "Bulk check-in item failed",
                            extra={**log_extra, "object_id": pk, "exception_type": type(ex).__name__},
                        )

                job.append_log(
                    f"Bulk check-in finished. Checked in: {success_count} | "
                    f"Skipped: {skipped_count} | Failures: {failure_count}"
                )

                if success_count == 0 and skipped_count == 0:
                    job.mark_failed("All asset check-ins failed.")
                    Notification.objects.create(
                        user=ctx.user,
                        subject=_("Bulk Check-in Failed"),
                        message=_("All hardware check-ins failed. View logs for details."),
                        level=Notification.LEVEL_DANGER,
                        target_url=reverse_job_detail(job.pk),
                    )
                    return TaskResult(
                        TaskStatus.TERMINAL,
                        "checkin.all_failed",
                        {"failed": failure_count, "total": len(asset_pks)},
                        "All asset check-ins failed.",
                        True,
                    )

                job.mark_completed(
                    result={
                        "checked_in": success_count,
                        "skipped": skipped_count,
                        "failed": failure_count,
                        "total": len(asset_pks),
                    }
                )
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Check-in Complete"),
                    message=_("Checked in %(count)s asset(s).") % {"count": success_count},
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if failure_count else TaskStatus.SUCCESS,
                    "checkin.partial" if failure_count else "checkin.completed",
                    {"checked_in": success_count, "skipped": skipped_count, "failed": failure_count},
                    user_visible=True,
                )

            # broad except: task-isolation: record a safe typed task-boundary failure
            except Exception as e:
                status = classify_task_error(e)
                logger.error("Bulk check-in task failed", extra={**log_extra, "exception_type": type(e).__name__})
                job.mark_failed(f"[{status.value}] checkin.boundary_failed")
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Check-in Error"),
                    message=_("The bulk check-in could not be completed. Code: checkin.boundary_failed"),
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(status, "checkin.boundary_failed", user_visible=True)
        # broad except: task-isolation: failures before Job resolution remain observable to the queue caller
        except Exception as exc:
            logger.error("Bulk check-in entry failed", extra={**log_extra, "exception_type": type(exc).__name__})
            return TaskResult(classify_task_error(exc), "checkin.entry_failed")
