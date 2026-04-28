# ==============================================================================
# ITAMbox Administrative Jobs Views
# ==============================================================================

import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import View
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType

from core.models import Job
from core.tables import JobTable
from itambox.views.generic import ObjectListView, ObjectDetailView

logger = logging.getLogger(__name__)


class JobListView(ObjectListView):
    model = Job
    table = JobTable
    template_name = 'core/jobs/job_list.html'
    title = 'Background Tasks & Jobs'

    def get_permission_required(self):
        # Allow standard users with view_job permissions to access
        return ('core.view_job',)


class JobDetailView(ObjectDetailView):
    model = Job
    template_name = 'core/jobs/job_detail.html'

    def get_permission_required(self):
        return ('core.view_job',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check if the Job has attached label ZIPs
        ct = ContentType.objects.get_for_model(Job)
        from core.models import FileAttachment
        attachments = FileAttachment.objects.filter(model=ct, object_id=self.object.pk)
        context['attachments'] = attachments
        context['title'] = f"Job Details: {self.object.name}"
        return context


class JobCancelView(LoginRequiredMixin, View):
    """
    Safely terminates a pending or running background job.
    """
    def post(self, request, pk):
        if not request.user.has_perm('core.change_job'):
            messages.error(request, "You do not have permission to modify background tasks.")
            return redirect('job_list')

        job = get_object_or_404(Job, pk=pk)
        
        if job.status in [Job.STATUS_PENDING, Job.STATUS_RUNNING]:
            job.mark_failed("Job manually cancelled by administrator.")
            messages.success(request, f"Job '{job.name}' cancelled successfully.")
        else:
            messages.warning(request, f"Job '{job.name}' is already finished.")

        return redirect('job_detail', pk=job.pk)
