import logging
import io
import zipfile
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile

from core.models import Job, Notification, FileAttachment
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)

def generate_single_label_graphic(asset, label_format):
    """
    Renders QR code or Barcode PNG bytes for the given asset.
    """
    import io
    buffer = io.BytesIO()
    
    if label_format == 'qr':
        import segno
        # Generate clean QR code
        qr = segno.make_qr(f"itambox://asset/{asset.pk}")
        qr.save(buffer, kind='png', scale=10)
    else:
        import barcode
        from barcode.writer import ImageWriter
        # Generate barcode
        CODING = barcode.get_barcode_class('code128')
        # Clean text
        code = CODING(asset.asset_tag or str(asset.pk), writer=ImageWriter())
        code.write(buffer)

    return buffer.getvalue()


def generate_label_batch_task(job_id, asset_pks, label_format, user_id, tenant_id=None):
    """
    Asynchronously generates QR-codes/barcodes for selected assets,
    packages them into a ZIP archive, and attaches it directly to the Job.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async label printing.")
                return

            job.mark_running()
            job.append_log("Starting label batch generation...")
            job.append_log(f"Format: {label_format} | Total assets: {len(asset_pks)}")

            try:
                from assets.models import Asset
                assets = Asset.objects.filter(pk__in=asset_pks)
                
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for asset in assets:
                        try:
                            img_data = generate_single_label_graphic(asset, label_format)
                            filename = f"label_{asset.asset_tag}_{label_format}.png"
                            zip_file.writestr(filename, img_data)
                            job.append_log(f" - Rendered label for {asset.asset_tag}")
                        except Exception as ex:
                            job.append_log(f" - Error rendering label for PK {asset.pk}: {str(ex)}")

                zip_buffer.seek(0)
                
                ct = ContentType.objects.get_for_model(Job)
                attachment = FileAttachment.objects.create(
                    model=ct,
                    object_id=job.pk,
                    name=f"labels_batch_{job.pk}.zip",
                    mime_type="application/zip"
                )
                attachment.file.save(f"labels_batch_{job.pk}.zip", ContentFile(zip_buffer.getvalue()))
                attachment.save()

                job.append_log(f"ZIP package generated and saved successfully: {attachment.file.name}")
                job.mark_completed(result={
                    'file_name': attachment.name,
                    'download_url': attachment.file.url
                })

                Notification.objects.create(
                    user=ctx.user,
                    subject="Label Generation Complete",
                    message=f"Successfully generated label batch zip for {assets.count()} asset(s). Click to download.",
                    level=Notification.LEVEL_SUCCESS,
                    target_url=attachment.file.url
                )

            except Exception as e:
                logger.exception("Exception during label batch generation task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject="Label Generation Failed",
                    message=f"An error occurred during barcode rendering: {str(e)}",
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )
        except Exception as e:
            logger.exception("Outer exception during label batch generation task")
