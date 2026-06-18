# ==============================================================================
# ITAMbox Administrative Jobs Views
# ==============================================================================

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import View

from core.managers import get_current_tenant
from core.models import Job
from core.tables import JobTable
from itambox.views.generic import ObjectListView, ObjectDetailView

logger = logging.getLogger(__name__)


def scoped_jobs(user):
    """
    Jobs visible to a user. Job has no tenant-scoping manager, so scope
    explicitly: superusers see everything (including system jobs without a
    tenant); everyone else only sees the active tenant's jobs.
    """
    if user.is_superuser:
        return Job.objects.all()
    tenant = get_current_tenant()
    if tenant is None:
        return Job.objects.none()
    return Job.objects.filter(tenant=tenant)


class JobListView(ObjectListView):
    model = Job
    table = JobTable
    template_name = 'core/jobs/job_list.html'
    title = gettext_lazy('Jobs')

    def get_permission_required(self):
        return ('core.view_job',)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset & scoped_jobs(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = dict(
            self.get_queryset().values_list('status').annotate(n=Count('pk'))
        )
        context['status_counts'] = {
            'pending': counts.get(Job.STATUS_PENDING, 0),
            'running': counts.get(Job.STATUS_RUNNING, 0),
            'completed': counts.get(Job.STATUS_COMPLETED, 0),
            'failed': counts.get(Job.STATUS_FAILED, 0),
        }
        context['has_active_jobs'] = bool(
            context['status_counts']['pending'] or context['status_counts']['running']
        )
        return context


class JobDetailView(ObjectDetailView):
    model = Job
    template_name = 'core/jobs/job_detail.html'

    def get_permission_required(self):
        return ('core.view_job',)

    def get_queryset(self):
        return scoped_jobs(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Label/export jobs attach their generated files to the Job record
        ct = ContentType.objects.get_for_model(Job)
        from extras.models import FileAttachment
        context['attachments'] = FileAttachment.objects.filter(
            model=ct, object_id=self.object.pk
        )
        context['title'] = self.object.name
        return context


class JobCancelView(LoginRequiredMixin, View):
    """
    Cancels a job that has not been picked up by a worker yet. Running jobs
    cannot be stopped — marking the row failed would not stop the task, and
    the worker would overwrite the status when it finishes.
    """
    def post(self, request, pk):
        if not request.user.has_perm('core.change_job'):
            messages.error(request, _("You do not have permission to cancel jobs."))
            return redirect('job_list')

        job = get_object_or_404(scoped_jobs(request.user), pk=pk)

        if job.cancel(_("Cancelled by %(user)s before execution.") % {'user': request.user}):
            messages.success(request, _("Job \"%(name)s\" cancelled.") % {'name': job.name})
        elif job.status == Job.STATUS_RUNNING:
            messages.warning(request, _("Job \"%(name)s\" is already running and can no longer be cancelled.") % {'name': job.name})
        else:
            messages.info(request, _("Job \"%(name)s\" has already finished.") % {'name': job.name})

        return redirect('job_detail', pk=job.pk)
