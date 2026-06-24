from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)

from ..models import Provider, ProviderRole, ProviderRoleTemplate
from ..forms import (
    ProviderForm, ProviderFilterForm,
    ProviderRoleForm, ProviderRoleFilterForm,
    ProviderRoleTemplateForm, ProviderRoleTemplateFilterForm,
)
from ..tables import ProviderTable, ProviderRoleTable, ProviderRoleTemplateTable
from ..filters import ProviderFilterSet, ProviderRoleFilterSet, ProviderRoleTemplateFilterSet


class ProviderAdminMixin(UserPassesTestMixin):
    """Restrict the Provider admin CRUD views to provider administrators: superusers, or
    provider staff holding the ``can_manage_provider_users`` capability. ``test_func``
    enforces this; ``get_permission_required`` returns an empty set so the generic
    PermissionRequiredMixin does not additionally require per-model view/add/change perms."""

    def test_func(self):
        user = self.request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        from core.auth.provider import has_provider_capability
        return has_provider_capability(user, 'manage_provider_users')

    def get_permission_required(self):
        return ()


# --------------------------------------------------------------------------- Provider
class ProviderListView(ProviderAdminMixin, ObjectListView):
    queryset = Provider.objects.all()
    filterset = ProviderFilterSet
    filterset_form = ProviderFilterForm
    table = ProviderTable
    action_buttons = ('add',)


class ProviderDetailView(ProviderAdminMixin, ObjectDetailView):
    queryset = Provider.objects.all()
    template_name = 'organization/providers/provider_detail.html'


class ProviderEditView(ProviderAdminMixin, ObjectEditView):
    queryset = Provider.objects.all()
    model = Provider
    model_form = ProviderForm
    template_name = 'generic/object_edit.html'


class ProviderDeleteView(ProviderAdminMixin, ObjectDeleteView):
    queryset = Provider.objects.all()
    model = Provider
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:provider_list')


# --------------------------------------------------------------------------- ProviderRole
class ProviderRoleListView(ProviderAdminMixin, ObjectListView):
    queryset = ProviderRole.objects.select_related('provider')
    filterset = ProviderRoleFilterSet
    filterset_form = ProviderRoleFilterForm
    table = ProviderRoleTable
    action_buttons = ('add',)


class ProviderRoleDetailView(ProviderAdminMixin, ObjectDetailView):
    queryset = ProviderRole.objects.select_related('provider', 'tenant_role_template')
    template_name = 'organization/providers/providerrole_detail.html'


class ProviderRoleEditView(ProviderAdminMixin, ObjectEditView):
    queryset = ProviderRole.objects.all()
    model = ProviderRole
    model_form = ProviderRoleForm
    template_name = 'generic/object_edit.html'


class ProviderRoleDeleteView(ProviderAdminMixin, ObjectDeleteView):
    queryset = ProviderRole.objects.all()
    model = ProviderRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:providerrole_list')


# --------------------------------------------------------------------------- ProviderRoleTemplate
class ProviderRoleTemplateListView(ProviderAdminMixin, ObjectListView):
    queryset = ProviderRoleTemplate.objects.select_related('provider')
    filterset = ProviderRoleTemplateFilterSet
    filterset_form = ProviderRoleTemplateFilterForm
    table = ProviderRoleTemplateTable
    action_buttons = ('add',)


class ProviderRoleTemplateDetailView(ProviderAdminMixin, ObjectDetailView):
    queryset = ProviderRoleTemplate.objects.select_related('provider')
    template_name = 'organization/providers/providerroletemplate_detail.html'


class ProviderRoleTemplateEditView(ProviderAdminMixin, ObjectEditView):
    queryset = ProviderRoleTemplate.objects.all()
    model = ProviderRoleTemplate
    model_form = ProviderRoleTemplateForm
    template_name = 'generic/object_edit.html'


class ProviderRoleTemplateDeleteView(ProviderAdminMixin, ObjectDeleteView):
    queryset = ProviderRoleTemplate.objects.all()
    model = ProviderRoleTemplate
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:providerroletemplate_list')


class ProviderRoleTemplateSyncView(LoginRequiredMixin, ProviderAdminMixin, View):
    """POST-only action that pushes a ProviderRoleTemplate's permissions out to the
    TenantRoles across the provider's tenants, then redirects back to the template detail."""

    def post(self, request, pk):
        template = get_object_or_404(ProviderRoleTemplate, pk=pk)
        created, updated = template.sync_to_tenant_roles()
        messages.success(
            request,
            _("Synced: %(created)d created, %(updated)d updated") % {
                'created': created,
                'updated': updated,
            },
        )
        return redirect('organization:providerroletemplate_detail', pk=template.pk)


# --------------------------------------------------------------------------- Provider dashboard
class ProviderDashboardView(ProviderAdminMixin, TemplateView):
    """Minimal provider overview: each provider with its tenant and active-staff counts."""

    template_name = 'organization/providers/provider_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        providers = []
        for provider in Provider.objects.all():
            providers.append({
                'provider': provider,
                'tenant_count': provider.tenants.count(),
                'staff_count': provider.memberships.filter(is_active=True).count(),
            })
        context['providers'] = providers
        return context
