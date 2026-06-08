import logging
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from core.models import Job, Notification
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)

def bulk_checkout_task(job_id, asset_pks, target_type_str, target_pk, user_id, notes, expected_checkin_date=None, tenant_id=None):
    """
    Asynchronously executes bulk checkout operations on selected hardware Assets
    utilizing select_for_update row-level locking to prevent race anomalies.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async checkout.")
                return

            job.mark_running()
            job.append_log("Initializing asynchronous bulk checkout pipeline...")
            job.append_log(f"Assets to process: {len(asset_pks)}")

            try:
                _CT_MAP = {
                    'assetholder': ('organization', 'assetholder'),
                    'asset':       ('assets', 'asset'),
                    'location':    ('organization', 'location'),
                }
                app_label, model_name = _CT_MAP.get(target_type_str, ('organization', target_type_str))
                target_model = ContentType.objects.get(
                    app_label=app_label,
                    model=model_name,
                ).model_class()

                target = target_model.objects.get(pk=target_pk)
                job.append_log(f"Checkout target assignee: {str(target)}")

                from assets.models import Asset
                from assets.services import checkout_asset

                # Map target_type_str to the correct checkout_asset keyword argument
                _TARGET_KWARG = {
                    'assetholder': 'holder',
                    'asset':       'asset_target',
                    'location':    'location',
                }
                target_kwarg = _TARGET_KWARG.get(target_type_str, 'location')

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
                                expected_checkin=expected_checkin_date
                            )
                            success_count += 1
                            job.append_log(f" - Asset {asset.asset_tag} ({asset.name}) checked out successfully.")
                    except Exception as ex:
                        failure_count += 1
                        job.append_log(f" - Failed to checkout Asset PK {pk}: {str(ex)}")

                job.append_log(f"Bulk checkout execution finished. Successes: {success_count} | Failures: {failure_count}")

                if success_count == 0:
                    job.mark_failed("All asset checkouts failed.")
                    Notification.objects.create(
                        user=ctx.user,
                        subject="Bulk Checkout Failed",
                        message="All hardware checkouts failed. View logs for error tracebacks.",
                        level=Notification.LEVEL_DANGER,
                        target_url=reverse_job_detail(job.pk)
                    )
                    return

                job.mark_completed(result={
                    'checked_out': success_count,
                    'failed': failure_count,
                    'total': len(asset_pks)
                })

                Notification.objects.create(
                    user=ctx.user,
                    subject="Bulk Checkout Complete",
                    message=f"Successfully checked out {success_count} asset(s).",
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk)
                )

            except Exception as e:
                logger.exception("Exception during bulk checkout task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject="Bulk Checkout Error",
                    message=f"A system exception occurred during the checkout: {str(e)}",
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )
        except Exception as e:
            logger.exception("Outer exception during bulk checkout task")
