"""Quick onboarding views — stage-3 remnant.

The single-form technician onboarding flow (``TechnicianQuickForm``) was folded
into the unified "Add member" flow: ``TechnicianQuickAddView`` is now a thin
redirect to ``memberships/add/?tenant=<msp pk>&preset=technician``. The nav item
"Add Technician" keeps pointing at this route, so bookmarks and menus survive.
"""
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View

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
    ).distinct().order_by('name')
    return [t for t in candidates if user.has_perm(perm, obj=t)]


class TechnicianQuickAddView(LoginRequiredMixin, View):
    """Thin redirect into the unified add-member flow with the technician preset.

    Resolves which managing (``is_provider``) tenant the actor onboards for: the
    active tenant when it qualifies, otherwise the first provider tenant where
    they hold ``organization.add_membership`` (superusers: any provider tenant).
    No form logic lives here anymore — the target ``MembershipCreateView``
    enforces the actual ``add_membership`` permission and the escalation guards.
    """
    http_method_names = ['get']

    def get(self, request):
        candidates = _provider_tenants_with_perm(
            request.user, 'organization.add_membership',
        )
        if getattr(request.user, 'is_superuser', False) and not candidates:
            candidates = list(Tenant._base_manager.filter(
                is_provider=True, deleted_at__isnull=True,
            ).order_by('name'))

        active = getattr(request, 'active_tenant', None)
        msp = None
        if active is not None and any(t.pk == active.pk for t in candidates):
            msp = active
        elif candidates:
            msp = candidates[0]

        if msp is None:
            raise PermissionDenied(
                _("No managing organization is available for technician onboarding.")
            )

        params = {'preset': 'technician', 'tenant': msp.pk}
        return redirect(
            f"{reverse('organization:membership_create')}?{urlencode(params)}"
        )
