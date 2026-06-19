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
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.translation import gettext as _

from core.models import Job, Notification
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_proceeds(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def bulk_dispose_task(job_id, asset_pks, user_id, tenant_id=None,
                      disposal_kwargs=None, proceeds_map=None):
    """Asynchronously dispose selected hardware assets."""
    disposal_kwargs = disposal_kwargs or {}
    proceeds_map = proceeds_map or {}

    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error("Job %s not found during async disposal.", job_id)
                return

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping disposal.", job_id)
                return
            job.append_log("Initializing asynchronous bulk disposal pipeline...")
            job.append_log(f"Assets to process: {len(asset_pks)}")

            try:
                from assets.models import Asset, AssetDisposal
                from assets.services import dispose_asset

                disposal_date = _parse_date(disposal_kwargs.get('disposal_date'))
                if disposal_date is None:
                    disposal_date = datetime.date.today()

                shared = {
                    'disposal_method': disposal_kwargs.get('disposal_method', 'destruction'),
                    'disposal_date': disposal_date,
                    'data_sanitization_method': disposal_kwargs.get('data_sanitization_method', 'none'),
                    'sanitization_certificate': disposal_kwargs.get('sanitization_certificate', ''),
                    'sanitized_by': disposal_kwargs.get('sanitized_by', ''),
                    'recipient': disposal_kwargs.get('recipient', ''),
                    'currency': disposal_kwargs.get('currency', ''),
                    'weee_compliant': disposal_kwargs.get('weee_compliant', False),
                    'notes': disposal_kwargs.get('notes', ''),
                }

                success_count = 0
                skipped_count = 0
                failure_count = 0

                for pk in asset_pks:
                    try:
                        asset = Asset.objects.get(pk=pk)

                        already_disposed = (
                            asset.disposed_at is not None
                            or AssetDisposal.all_objects.filter(asset=asset).exists()
                        )
                        if already_disposed:
                            skipped_count += 1
                            job.append_log(f" - Asset {asset.asset_tag} ({asset.name}) skipped (already disposed).")
                            continue

                        proceeds = _parse_proceeds(proceeds_map.get(str(pk)))
                        dispose_asset(asset=asset, user=ctx.user, proceeds=proceeds, **shared)
                        success_count += 1
                        job.append_log(f" - Asset {asset.asset_tag} ({asset.name}) disposed.")
                    except Exception as ex:
                        failure_count += 1
                        job.append_log(f" - Failed to dispose Asset PK {pk}: {ex}")

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
                    return

                job.mark_completed(result={
                    'disposed': success_count,
                    'skipped': skipped_count,
                    'failed': failure_count,
                    'total': len(asset_pks),
                })
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Disposal Complete"),
                    message=_("Disposed %(count)s asset(s).") % {'count': success_count},
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk),
                )

            except Exception as e:
                logger.exception("Exception during bulk disposal task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Disposal Error"),
                    message=_("A system exception occurred during disposal: %(error)s") % {'error': str(e)},
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk),
                )
        except Exception:
            logger.exception("Outer exception during bulk disposal task")
