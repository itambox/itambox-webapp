import datetime
import logging
from collections.abc import Sequence

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.translation import gettext_lazy as _

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


def bulk_checkout_task(
    job_id: int,
    asset_pks: Sequence[int | str],
    target_type_str: str,
    target_pk: int | str,
    user_id: int | None,
    notes: str,
    expected_checkin_date: str | datetime.date | None = None,
    tenant_id: int | None = None,
    status_id: int | str | None = None,
    checkout_date: str | datetime.date | None = None,
) -> TaskResult:
    """
    Asynchronously executes bulk checkout operations on selected hardware Assets
    utilizing select_for_update row-level locking to prevent race anomalies.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="assets.bulk_checkout") as ctx:
        log_extra = {**ctx.log_context, "job_id": job_id}
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Bulk checkout job not found", extra=log_extra)
                return TaskResult(TaskStatus.TERMINAL, "checkout.job_not_found")

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping checkout.", job_id)
                return TaskResult(TaskStatus.SKIPPED, "checkout.job_not_pending")
            job.append_log("Initializing asynchronous bulk checkout pipeline...")
            job.append_log(f"Assets to process: {len(asset_pks)}")

            try:
                _CT_MAP = {
                    "assetholder": ("organization", "assetholder"),
                    "asset": ("assets", "asset"),
                    "location": ("organization", "location"),
                }
                app_label, model_name = _CT_MAP.get(target_type_str, ("organization", target_type_str))
                target_model = ContentType.objects.get(
                    app_label=app_label,
                    model=model_name,
                ).model_class()

                target = target_model.objects.get(pk=target_pk)
                job.append_log("Checkout target resolved.")

                from assets.models import Asset, StatusLabel
                from assets.services import checkout_asset

                # Map target_type_str to the correct checkout_asset keyword argument
                _TARGET_KWARG = {
                    "assetholder": "holder",
                    "asset": "asset_target",
                    "location": "location",
                }
                target_kwarg = _TARGET_KWARG.get(target_type_str, "location")

                status = StatusLabel.objects.filter(pk=status_id).first() if status_id else None
                resolved_checkin = _parse_date(expected_checkin_date)
                resolved_checkout = _parse_date(checkout_date)

                success_count = 0
                failure_count = 0

                for pk in asset_pks:
                    try:
                        with transaction.atomic():
                            asset = Asset.objects.select_for_update().get(pk=pk)
                            checkout_asset(
                                asset=asset,
                                **{target_kwarg: target},
                                user=ctx.user,
                                notes=notes,
                                status=status,
                                expected_checkin=resolved_checkin,
                                checkout_date=resolved_checkout,
                            )
                            success_count += 1
                            job.append_log(f" - Asset PK {pk} checked out successfully.")
                    # broad except: boundary-isolation: one asset failure must not abort the requested batch
                    except Exception as ex:
                        failure_count += 1
                        job.append_log(f" - Asset PK {pk} failed [checkout.item_failed].")
                        logger.warning(
                            "Bulk checkout item failed",
                            extra={**log_extra, "object_id": pk, "exception_type": type(ex).__name__},
                        )

                job.append_log(
                    f"Bulk checkout execution finished. Successes: {success_count} | Failures: {failure_count}"
                )

                if success_count == 0:
                    job.mark_failed("All asset checkouts failed.")
                    Notification.objects.create(
                        user=ctx.user,
                        subject=_("Bulk Checkout Failed"),
                        message=_("All hardware checkouts failed. View logs for error tracebacks."),
                        level=Notification.LEVEL_DANGER,
                        target_url=reverse_job_detail(job.pk),
                    )
                    return TaskResult(
                        TaskStatus.TERMINAL, "checkout.all_failed", {"failed": failure_count}, user_visible=True
                    )

                job.mark_completed(
                    result={"checked_out": success_count, "failed": failure_count, "total": len(asset_pks)}
                )

                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Checkout Complete"),
                    message=_("Successfully checked out %(count)s asset(s).") % {"count": success_count},
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if failure_count else TaskStatus.SUCCESS,
                    "checkout.partial" if failure_count else "checkout.completed",
                    {"checked_out": success_count, "failed": failure_count},
                    user_visible=True,
                )

            # broad except: task-isolation: record a safe typed task-boundary failure
            except Exception as e:
                status = classify_task_error(e)
                logger.error("Bulk checkout task failed", extra={**log_extra, "exception_type": type(e).__name__})
                job.mark_failed(f"[{status.value}] checkout.boundary_failed")
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Checkout Error"),
                    message=_("The bulk checkout could not be completed. Code: checkout.boundary_failed"),
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk),
                )
                return TaskResult(status, "checkout.boundary_failed", user_visible=True)
        # broad except: task-isolation: failures before Job resolution remain observable to the queue caller
        except Exception as exc:
            logger.error("Bulk checkout entry failed", extra={**log_extra, "exception_type": type(exc).__name__})
            return TaskResult(classify_task_error(exc), "checkout.entry_failed")
