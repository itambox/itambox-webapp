"""Background task: scanner-driven bulk disposal of hardware assets.

Mirrors ``bulk_checkout_task``. Each asset is disposed via the canonical
``dispose_asset`` service (which auto-checks-in, records an ``AssetDisposal``,
freezes book value and archives the asset). Already-disposed assets are skipped
so a re-run never overwrites an existing disposal record. ``proceeds`` is
per-asset (``proceeds_map``); when absent the service freezes the depreciated
book value.
"""

import datetime
import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

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


def _parse_proceeds(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    # Negative proceeds are invalid (would push book value negative) — drop them.
    return parsed if parsed >= 0 else None


def bulk_dispose_task(
    job_id: int,
    asset_pks: Sequence[int | str],
    user_id: int | None,
    tenant_id: int | None = None,
    disposal_kwargs: Mapping[str, object] | None = None,
    proceeds_map: Mapping[str, object] | None = None,
) -> TaskResult:
    """Asynchronously dispose selected hardware assets."""
    disposal_kwargs = disposal_kwargs or {}
    proceeds_map = proceeds_map or {}

    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="assets.bulk_disposal") as ctx:
        log_extra = {**ctx.log_context, "job_id": job_id}
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Bulk disposal job not found", extra=log_extra)
                return TaskResult(TaskStatus.TERMINAL, "disposal.job_not_found")

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping disposal.", job_id)
                return TaskResult(TaskStatus.SKIPPED, "disposal.job_not_pending")
            job.append_log("Initializing asynchronous bulk disposal pipeline...")
            job.append_log(f"Assets to process: {len(asset_pks)}")

            try:
                from assets.models import Asset, AssetDisposal
                from assets.services import dispose_asset

                disposal_date = _parse_date(disposal_kwargs.get("disposal_date"))
                if disposal_date is None:
                    disposal_date = datetime.date.today()

                shared = {
                    "disposal_method": disposal_kwargs.get("disposal_method", "destruction"),
                    "disposal_date": disposal_date,
                    "data_sanitization_method": disposal_kwargs.get("data_sanitization_method", "none"),
                    "sanitization_certificate": disposal_kwargs.get("sanitization_certificate", ""),
                    "sanitized_by": disposal_kwargs.get("sanitized_by", ""),
                    "recipient": disposal_kwargs.get("recipient", ""),
                    "currency": disposal_kwargs.get("currency", ""),
                    "weee_compliant": disposal_kwargs.get("weee_compliant", False),
                    "notes": disposal_kwargs.get("notes", ""),
                }

                success_count = 0
                skipped_count = 0
                failure_count = 0

                for pk in asset_pks:
                    try:
                        asset = Asset.objects.get(pk=pk)

                        already_disposed = (
                            asset.disposed_at is not None or AssetDisposal.all_objects.filter(asset=asset).exists()
                        )
                        if already_disposed:
                            skipped_count += 1
                            job.append_log(f" - Asset PK {pk} skipped (already disposed).")
                            continue

                        proceeds = _parse_proceeds(proceeds_map.get(str(pk)))
                        dispose_asset(asset=asset, user=ctx.user, proceeds=proceeds, **shared)
                        success_count += 1
                        job.append_log(f" - Asset PK {pk} disposed.")
                    # broad except: boundary-isolation: one asset failure must not abort the requested batch
                    except Exception as ex:
                        failure_count += 1
                        job.append_log(f" - Asset PK {pk} failed [disposal.item_failed].")
                        logger.warning(
                            "Bulk disposal item failed",
                            extra={**log_extra, "object_id": pk, "exception_type": type(ex).__name__},
                        )

                job.append_log(
                    f"Bulk disposal finished. Disposed: {success_count} | "
                    f"Skipped: {skipped_count} | Failures: {failure_count}"
                )

                if success_count == 0 and skipped_count == 0:
                    job.mark_failed("All asset disposals failed.")
                    Notification.objects.create(
                        user=ctx.user,
                        subject=_("Bulk Disposal Failed"),
                        message=_("All hardware disposals failed. View logs for details."),
                        level=Notification.LEVEL_DANGER,
                        target_url=reverse_job_detail(job.pk),
                    )
                    return TaskResult(
                        TaskStatus.TERMINAL, "disposal.all_failed", {"failed": failure_count}, user_visible=True
                    )

                job.mark_completed(
                    result={
                        "disposed": success_count,
                        "skipped": skipped_count,
                        "failed": failure_count,
                        "total": len(asset_pks),
                    }
                )
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Disposal Complete"),
                    message=_("Disposed %(count)s asset(s).") % {"count": success_count},
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if failure_count else TaskStatus.SUCCESS,
                    "disposal.partial" if failure_count else "disposal.completed",
                    {"disposed": success_count, "skipped": skipped_count, "failed": failure_count},
                    user_visible=True,
                )

            # broad except: task-isolation: record a safe typed task-boundary failure
            except Exception as e:
                status = classify_task_error(e)
                logger.error("Bulk disposal task failed", extra={**log_extra, "exception_type": type(e).__name__})
                job.mark_failed(f"[{status.value}] disposal.boundary_failed")
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Disposal Error"),
                    message=_("The bulk disposal could not be completed. Code: disposal.boundary_failed"),
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(status, "disposal.boundary_failed", user_visible=True)
        # broad except: task-isolation: failures before Job resolution remain observable to the queue caller
        except Exception as exc:
            logger.error("Bulk disposal entry failed", extra={**log_extra, "exception_type": type(exc).__name__})
            return TaskResult(classify_task_error(exc), "disposal.entry_failed")
