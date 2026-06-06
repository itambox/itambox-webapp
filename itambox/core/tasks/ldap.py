import logging
from django.core.management import call_command
from core.models import Job, Notification
from .context import TaskContext
from .utils import reverse_job_detail

logger = logging.getLogger(__name__)

class JobLogStream:
    def __init__(self, job):
        self.job = job
        self.buffer = ""

    def write(self, message):
        self.buffer += message
        if "\n" in self.buffer:
            lines = self.buffer.split("\n")
            self.buffer = lines.pop()
            for line in lines:
                cleaned = line.strip()
                if cleaned:
                    self.job.append_log(cleaned)

    def flush(self):
        if self.buffer.strip():
            self.job.append_log(self.buffer.strip())
            self.buffer = ""

def sync_tenant_ldap_task(job_id, tenant_slug, user_id, tenant_id=None):
    """
    Asynchronously executes tenant LDAP directory synchronization.
    Runs the 'sync_tenant_ldap' management command and streams output to Job logs.
    """
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            try:
                job = Job.objects.get(pk=job_id)
            except Job.DoesNotExist:
                logger.error(f"Job {job_id} not found during async LDAP sync.")
                return

            job.mark_running()
            job.append_log(f"Initializing LDAP directory sync for tenant: {tenant_slug}...")

            log_stream = JobLogStream(job)

            try:
                # Call command and pipe output to log_stream
                call_command('sync_tenant_ldap', tenant=tenant_slug, stdout=log_stream, stderr=log_stream)
                log_stream.flush()

                job.append_log("LDAP directory sync execution finished.")
                job.mark_completed(result={'status': 'success'})

                Notification.objects.create(
                    user=ctx.user,
                    subject="LDAP Sync Complete",
                    message=f"LDAP directory sync for tenant '{tenant_slug}' completed successfully.",
                    level=Notification.LEVEL_SUCCESS,
                    target_url=reverse_job_detail(job.pk)
                )

            except Exception as e:
                log_stream.flush()
                logger.exception("Exception during LDAP sync task")
                job.mark_failed(str(e))
                Notification.objects.create(
                    user=ctx.user,
                    subject="LDAP Sync Failed",
                    message=f"LDAP directory sync for tenant '{tenant_slug}' failed: {str(e)}",
                    level=Notification.LEVEL_DANGER,
                    target_url=reverse_job_detail(job.pk)
                )

        except Exception as e:
            logger.exception("Outer exception during LDAP sync task")
