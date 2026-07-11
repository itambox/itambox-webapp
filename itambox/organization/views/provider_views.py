"""Quick onboarding views.

The Provider CRUD views died with the ``Provider`` model: a provider is now a
``Tenant`` with ``is_provider=True``, administered through the regular tenant
screens. What remains here is the single-form technician onboarding flow
(kept until the stage-3 grants UX replaces it).
"""
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from ..forms import TechnicianQuickForm
from ..models import Tenant


def _provider_tenants_with_perm(user, perm):
    """The ``is_provider`` tenants the user belongs to and holds ``perm`` in.

    Uses ``_base_manager``: this flow runs provider-side, where the tenant-scoped
    default manager silently fails closed to ``.none()``.
    """
    if not (user and user.is_authenticated):
        return []
    candidates = Tenant._base_manager.filter(
        is_provider=True,
        deleted_at__isnull=True,
        memberships__user=user,
        memberships__is_active=True,
    ).distinct()
    return [t for t in candidates if user.has_perm(perm, obj=t)]


class TechnicianQuickAddView(UserPassesTestMixin, FormView):
    """Single-form technician onboarding at a managing (``is_provider``) tenant.

    Gate: superuser, or ``organization.add_membership`` held at one of the
    ``is_provider`` tenants the user belongs to.
    """
    template_name = 'organization/providers/technician_quick.html'
    form_class = TechnicianQuickForm

    def test_func(self):
        user = self.request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        return bool(_provider_tenants_with_perm(user, 'organization.add_membership'))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        # Defense in depth — the form's clean() enforces the same gate.
        organization = form.cleaned_data['organization']
        if not self.request.user.is_superuser and not self.request.user.has_perm(
            'organization.add_membership', obj=organization,
        ):
            messages.error(
                self.request,
                _("You do not have permission to onboard staff for this managing tenant."),
            )
            return self.form_invalid(form)

        user, membership = form.save()
        if form.cleaned_data.get('role') is None:
            # No role was chosen (allowed for a first hire): the membership carries zero
            # permissions until a role assignment is created. Steer the admin straight
            # into role creation, deep-linked to the managing tenant so the new role is
            # owned there.
            messages.warning(
                self.request,
                _("Onboarded %(user)s as staff of %(tenant)s, but they have NO "
                  "permissions yet. Create and assign a role to grant access.") % {
                    'user': user, 'tenant': membership.tenant,
                },
            )
            role_create_url = (
                reverse('organization:role_create')
                + f'?tenant={membership.tenant_id}'
            )
            return redirect(role_create_url)
        messages.success(
            self.request,
            _("Onboarded %(user)s as staff of %(tenant)s.") % {
                'user': user, 'tenant': membership.tenant,
            },
        )
        return redirect(reverse('organization:membership_detail', kwargs={'pk': membership.pk}))
