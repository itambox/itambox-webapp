"""Provider CRUD + onboarding views (unified RBAC)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)

from ..models import Provider
from ..forms import (
    ProviderForm, ProviderFilterForm,
    TechnicianQuickForm,
)
from ..tables import ProviderTable
from ..filters import ProviderFilterSet


class ProviderAdminMixin(UserPassesTestMixin):
    """Restrict Provider-admin views to users holding ``organization.manage_provider``
    against any Provider (or to superusers).

    Resolution is unified: the capability is a plain Django permission carried by a
    provider-scoped Role attached to a staff Membership and resolved through
    ``user.has_perm()``.
    """

    def test_func(self):
        user = self.request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        from core.auth.provider import has_provider_capability
        return has_provider_capability(user, 'manage_provider')

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


# The provider-managed cross-tenant view was folded into ``TenantListView`` (the main
# ``/tenants/`` list): provider-admins opt into the cross-provider set via
# ``?all_providers=true`` plus a "Managed by provider" filter + Provider column, instead
# of a separate ``customer-tenants/`` route. See RBAC_STABILIZATION_REVIEW.md §8.


# --------------------------------------------------------------------------- Quick onboarding
class TechnicianQuickAddView(ProviderAdminMixin, FormView):
    """Single-form provider technician onboarding."""
    template_name = 'organization/providers/technician_quick.html'
    form_class = TechnicianQuickForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        provider = form.cleaned_data['provider']
        if not self.request.user.is_superuser and not self.request.user.has_perm('organization.manage_staff', obj=provider):
            messages.error(self.request, _("You do not have permission to manage staff for this provider."))
            return self.form_invalid(form)

        user, membership = form.save()
        if form.cleaned_data.get('role') is None:
            # No role was chosen (allowed for a first hire): the membership carries zero
            # permissions until a role is attached. Steer the admin straight into role
            # creation, deep-linked to this provider so the new role is provider-scoped.
            messages.warning(
                self.request,
                _("Onboarded %(user)s as staff of %(provider)s, but they have NO "
                  "permissions yet. Create and assign a role to grant access.") % {
                    'user': user, 'provider': membership.provider,
                },
            )
            role_create_url = (
                reverse('organization:role_create')
                + f'?provider={membership.provider.pk}'
            )
            return redirect(role_create_url)
        messages.success(
            self.request,
            _("Onboarded %(user)s as staff of %(provider)s.") % {
                'user': user, 'provider': membership.provider,
            },
        )
        return redirect(reverse('organization:membership_detail', kwargs={'pk': membership.pk}))



