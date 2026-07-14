"""Operator UI for cross-tenant resource grants (ADR-0001 phase 4b).

Grants are created FROM a concrete stock pool (the share action on a stock
row binds resource type + id in the URL) — the owner tenant is always
derived from the pool's location, never client-supplied. Revocation is the
generic delete flow (TenantResourceGrant.delete soft-revokes).
"""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from core.managers import get_current_tenant
from itambox.views.generic import ObjectDeleteView, ObjectEditView, ObjectListView

from .. import tables
from ..forms import TenantResourceGrantForm
from ..models import TenantResourceGrant
from ..access import get_ancestor_tenant_group_ids


def _grants_involving(tenant):
    """Live grants given BY or received BY ``tenant`` (direct or via group)."""
    q = Q(tenant=tenant) | Q(grantee_tenant=tenant)
    ancestor_ids = get_ancestor_tenant_group_ids(tenant.group_id, live_only=True)
    if ancestor_ids:
        q |= Q(grantee_tenant_group_id__in=ancestor_ids)
    return TenantResourceGrant.objects.filter(q)


class TenantResourceGrantListView(ObjectListView):
    queryset = TenantResourceGrant.objects.none()
    table = tables.TenantResourceGrantTable
    action_buttons = ()

    def get_queryset(self):
        # The grant manager is deliberately unscoped (authorization infra);
        # this view scopes explicitly: everything involving the active tenant.
        tenant = get_current_tenant()
        if tenant is None:
            if self.request.user.is_superuser:
                return TenantResourceGrant.objects.select_related(
                    'tenant', 'grantee_tenant', 'grantee_tenant_group',
                    'resource_type', 'granted_by',
                )
            return TenantResourceGrant.objects.none()
        return _grants_involving(tenant).select_related(
            'tenant', 'grantee_tenant', 'grantee_tenant_group',
            'resource_type', 'granted_by',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Resource Grants')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (None, _('Tenancy')),
            (None, _('Resource Grants')),
        ]
        return context


class TenantResourceGrantCreateView(ObjectEditView):
    """Share ONE stock pool: /resource-grants/add/<content_type_id>/<resource_id>/."""
    model = TenantResourceGrant
    model_form = TenantResourceGrantForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'organization:tenantresourcegrant_list'

    def _resolve_pool(self):
        if getattr(self, '_pool', None) is None:
            ct = get_object_or_404(ContentType, pk=self.kwargs['content_type_id'])
            if f'{ct.app_label}.{ct.model}' not in TenantResourceGrant.APPROVED_RESOURCE_MODELS:
                raise Http404()
            model = ct.model_class()
            stock = model._base_manager.filter(
                pk=self.kwargs['resource_id'],
            ).select_related('location__tenant').first()
            if stock is None or stock.location.tenant_id is None:
                raise Http404()
            self._pool = (ct, stock, stock.location.tenant)
        return self._pool

    def has_permission(self):
        # Anchor at the pool's OWNER tenant: only someone holding the add
        # permission there may share the owner's stock.
        _ct, _stock, owner = self._resolve_pool()
        return self.request.user.has_perms(
            ('organization.add_tenantresourcegrant',), obj=owner,
        )

    def get_form(self, form_class=None):
        ct, stock, owner = self._resolve_pool()
        # Bind the non-form fields BEFORE validation (ObjectEditView.get_form
        # passes self.object as the form instance) so the model's full clean
        # (ownership-through-location, allowlist) produces form errors instead
        # of save-time surprises.
        self.object = TenantResourceGrant(
            tenant=owner,
            resource_type=ct,
            resource_id=stock.pk,
            granted_by=self.request.user,
        )
        return super().get_form(form_class)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        _ct, _stock, owner = self._resolve_pool()
        kwargs['owner_tenant'] = owner
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _ct, stock, owner = self._resolve_pool()
        context['title'] = _('Share stock pool: %(stock)s') % {'stock': stock}
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('organization:tenantresourcegrant_list'), _('Resource Grants')),
            (None, _('Share')),
        ]
        return context


class TenantResourceGrantRevokeView(ObjectDeleteView):
    """Revocation = the generic delete flow; the model soft-revokes."""
    model = TenantResourceGrant
    queryset = TenantResourceGrant.objects.all()
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenantresourcegrant_list')

    def get_queryset(self):
        # Owner-side only: the grantee cannot revoke (nor 403-probe) a grant.
        tenant = get_current_tenant()
        if tenant is None:
            if self.request.user.is_superuser:
                return TenantResourceGrant.objects.all()
            return TenantResourceGrant.objects.none()
        return TenantResourceGrant.objects.filter(tenant=tenant)
