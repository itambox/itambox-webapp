# ==============================================================================
# ITAMbox Administrative Jobs Views
# ==============================================================================

import logging

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import View

from core.managers import get_current_tenant, get_current_tenant_group
from core.models import Job
from core.tables import JobTable
from core.tenant_scope import accessible_tenant_ids, get_descendant_tenant_group_ids
from itambox.views.generic import ObjectDetailView, ObjectListView

logger = logging.getLogger(__name__)


def scoped_jobs(user):
    """
    Jobs visible to a user. Job has no tenant-scoping manager, so scope
    explicitly: superusers see everything (including system jobs without a
    tenant); everyone else sees the active tenant's jobs. A tenant-group scope
    is restricted to the selected group's live subtree, while the aggregate
    scope spans the complete canonical accessible tenant set.
    """
    if user.is_superuser:
        return Job.objects.all()
    tenant = get_current_tenant()
    if tenant is not None:
        allowed_scope = [tenant.pk]
        return Job.objects.filter(tenant=tenant).filter(
            Q(data__scope_tenant_ids__isnull=True) | Q(data__scope_tenant_ids__contained_by=allowed_scope)
        )

    tenant_ids = set(accessible_tenant_ids(user))
    group = get_current_tenant_group()
    if group is not None:
        Tenant = apps.get_model("organization", "Tenant")
        group_tenant_ids = Tenant._base_manager.filter(
            group_id__in=get_descendant_tenant_group_ids(group.pk, live_only=True),
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
        tenant_ids &= set(group_tenant_ids)
    if not tenant_ids:
        return Job.objects.none()
    allowed_scope = sorted(tenant_ids)
    return Job.objects.filter(tenant_id__in=allowed_scope).filter(
        Q(data__scope_tenant_ids__isnull=True) | Q(data__scope_tenant_ids__contained_by=allowed_scope)
    )


class JobListView(ObjectListView):
    model = Job
    table = JobTable
    template_name = "core/jobs/job_list.html"
    title = gettext_lazy("Jobs")

    def get_permission_required(self):
        return ("core.view_job",)

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset & scoped_jobs(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = dict(self.get_queryset().values_list("status").annotate(n=Count("pk")))
        context["status_counts"] = {
            "pending": counts.get(Job.STATUS_PENDING, 0),
            "running": counts.get(Job.STATUS_RUNNING, 0),
            "completed": counts.get(Job.STATUS_COMPLETED, 0),
            "failed": counts.get(Job.STATUS_FAILED, 0),
        }
        context["has_active_jobs"] = bool(context["status_counts"]["pending"] or context["status_counts"]["running"])
        return context


class JobDetailView(ObjectDetailView):
    model = Job
    template_name = "core/jobs/job_detail.html"

    def get_permission_required(self):
        return ("core.view_job",)

    def get_queryset(self):
        return scoped_jobs(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Label/export jobs attach their generated files to the Job record
        ct = ContentType.objects.get_for_model(Job)
        from extras.models import FileAttachment

        context["attachments"] = FileAttachment.objects.filter(model=ct, object_id=self.object.pk)
        context["title"] = self.object.name
        return context


class JobCancelView(LoginRequiredMixin, View):
    """
    Cancels a job that has not been picked up by a worker yet. Running jobs
    cannot be stopped — marking the row failed would not stop the task, and
    the worker would overwrite the status when it finishes.
    """

    def post(self, request, pk):
        # In the aggregate scope the permission check is tenant-bound: the RBAC
        # backend cannot resolve an object-less has_perm there, so resolve the
        # job first and check against its tenant. Concrete scope keeps the
        # original object-less check so view-only members still get the friendly
        # redirect instead of a 404.
        tenant = get_current_tenant()
        if tenant is None:
            job = get_object_or_404(scoped_jobs(request.user), pk=pk)
            if not request.user.has_perm("core.change_job", obj=job.tenant):
                messages.error(request, _("You do not have permission to cancel jobs."))
                return redirect("job_list")
        elif not request.user.has_perm("core.change_job"):
            messages.error(request, _("You do not have permission to cancel jobs."))
            return redirect("job_list")
        else:
            job = get_object_or_404(scoped_jobs(request.user), pk=pk)

        if job.cancel(_("Cancelled by %(user)s before execution.") % {"user": request.user}):
            messages.success(request, _('Job "%(name)s" cancelled.') % {"name": job.name})
        elif job.status == Job.STATUS_RUNNING:
            messages.warning(
                request, _('Job "%(name)s" is already running and can no longer be cancelled.') % {"name": job.name}
            )
        else:
            messages.info(request, _('Job "%(name)s" has already finished.') % {"name": job.name})

        return redirect("job_detail", pk=job.pk)
