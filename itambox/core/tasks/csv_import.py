import logging
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.models import Job, Notification
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)

def import_csv_task(job_id, rows_data, app_label, model_name, user_id, tenant_id=None):
    """
    Asynchronously imports parsed CSV/YAML rows into a target model
    using the dynamic BulkImportForm schema inside database transactions.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async import.")
                return

            if not job.mark_running():
                logger.info("Job %s is no longer pending (cancelled?); skipping import.", job_id)
                return
            job.append_log("Initializing asynchronous import pipeline...")
            job.append_log(f"Target model: {app_label}.{model_name} | Row Count: {len(rows_data)}")

            try:
                model = ContentType.objects.get(app_label=app_label, model=model_name).model_class()
                if not model:
                    raise ValidationError(
                        _("Target model %(app_label)s.%(model_name)s could not be resolved.")
                        % {'app_label': app_label, 'model_name': model_name}
                    )

                from itambox.views.generic import ObjectImportView
                view_instance = ObjectImportView()
                view_instance.model = model
                ImportFormClass = view_instance.get_form_class()
                
                form = ImportFormClass()
                form._rows_data = rows_data

                job.append_log("Validating and importing records inside transaction...")
                
                with transaction.atomic():
                    imported_count, errors = form.import_data()

                job.append_log(f"Import finished. Successfully imported: {imported_count} record(s).")
                
                if errors:
                    job.append_log(f"Encountered {len(errors)} error(s) during processing:")
                    for err in errors:
                        job.append_log(f" - {err}")
                    
                    if imported_count == 0:
                        job.mark_failed("All records failed to import due to validation errors.")
                        Notification.objects.create(
                            user=ctx.user,
                            subject=_("Bulk Import Failed"),
                            message=_("Failed to import CSV/YAML data to %(model)s. View job logs for details.")
                            % {'model': model._meta.verbose_name_plural},
                            level=Notification.LEVEL_DANGER,
                            target_url=reverse_job_detail(job.pk)
                        )
                        return

                job.mark_completed(result={
                    'imported': imported_count,
                    'failed': len(errors),
                    'total': len(rows_data)
                })

                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Import Complete"),
                    message=_("Successfully imported %(count)s record(s) to %(model)s.")
                    % {'count': imported_count, 'model': model._meta.verbose_name_plural},
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk)
                )

            except Exception as e:
                # The raw exception (str(e)) can carry DB driver text / SQL
                # fragments / internal paths — log it server-side only and surface
                # a generic message to the user. The job log carries the details
                # for an operator who can already see them.
                logger.exception("Exception during async import task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject=_("Bulk Import Error"),
                    message=_("A system error occurred during the import. View job logs for details."),
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )
        except Exception as e:
            logger.exception("Outer exception during async import task")
