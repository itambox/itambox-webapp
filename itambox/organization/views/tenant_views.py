from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.decorators import login_required
from django.views import View
from django.views.decorators.http import require_POST

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from itambox.utils import get_paginate_count
from itambox.panels import Panel
from assets.tables import AssetTable, AccessoryTable, ConsumableTable, KitTable
from licenses.tables import LicenseTable
from subscriptions.tables import SubscriptionTable

from ..models import Tenant
from ..forms import TenantForm, TenantFilterForm
from ..tables import TenantTable, SiteTable, LocationTable, AssetHolderTable
from ..filters import TenantFilterSet
from ..access import tenant_access_report
from django_tables2 import RequestConfig


class TenantListView(ObjectListView):
    """Tenant-scoped by default, always — an MSP admin no longer widens this list
    with ``?all_providers=true`` (retired: RBAC stage-3 §3). Superusers still see
    every tenant via the normal unscoped path (no active tenant context ⇒
    ``filter_by_tenant()`` is a no-op). MSP staff reach their customer tenants
    through the switcher (``organization.access.accessible_tenant_ids``) or the
    "Managed Tenants" tab on their own provider tenant's detail page.
    """
    queryset = Tenant.objects.select_related('group', 'managed_by').prefetch_related('tags').annotate(
        site_count=Count('sites', distinct=True),
        location_count=Count('locations', distinct=True),
    )
    filterset = TenantFilterSet
    filterset_form = TenantFilterForm
    table = TenantTable
    action_buttons = ('add',)


class TenantDetailView(ObjectDetailView):
    queryset = Tenant.objects.select_related('group').prefetch_related('tags')

    layout = (
        ((Panel('info', _('Tenant Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.get_object()

        context['tenant_site_count'] = tenant.sites.count()
        context['tenant_location_count'] = tenant.locations.count()
        context['tenant_assetholder_count'] = tenant.asset_holders.count()
        context['tenant_asset_count'] = tenant.assets.count()
        context['tenant_accessory_count'] = tenant.accessories.count()
        context['tenant_consumable_count'] = tenant.consumables.count()
        context['tenant_component_count'] = tenant.components.count()
        
        from django.conf import settings
        from compliance.models import CustodyTemplate
        context['tenant_custody_count'] = CustodyTemplate.objects.filter(tenant=tenant).count()
        context['tenant_license_count'] = tenant.licenses.count()
        context['tenant_kit_count'] = tenant.kits.count()
        context['tenant_subscription_count'] = tenant.subscriptions_org.count()

        # Managed tenants are a DIFFERENT tenant than whichever one is active for
        # this request — the scoped default manager (``tenant.managed_tenants``)
        # would silently return none of them, so count via ``_base_manager``.
        context['tenant_managed_count'] = (
            Tenant._base_manager.filter(managed_by_id=tenant.pk, deleted_at__isnull=True).count()
            if tenant.is_provider else 0
        )

        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_LDAP_CONFIGS', {})
        context['has_ldap'] = tenant.slug in tenant_configs
        return context

    def get_tab_sites(self, request):
        tenant = self.get_object()
        table = SiteTable(tenant.sites.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Sites'),
            'empty_icon': 'mdi-office-building',
            'empty_text': _('No sites found for this tenant.'),
        })

    def get_tab_locations(self, request):
        tenant = self.get_object()
        table = LocationTable(tenant.locations.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Locations'),
            'empty_icon': 'mdi-map-marker-outline',
            'empty_text': _('No locations found for this tenant.'),
        })

    def get_tab_assetholders(self, request):
        tenant = self.get_object()
        table = AssetHolderTable(tenant.asset_holders.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Asset Holders'),
            'empty_icon': 'mdi-account-group-outline',
            'empty_text': _('No asset holders found for this tenant.'),
        })

    def get_tab_assets(self, request):
        tenant = self.get_object()
        table = AssetTable(tenant.assets.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Assets'),
            'empty_icon': 'mdi-laptop',
            'empty_text': _('No assets found for this tenant.'),
        })

    def get_tab_accessories(self, request):
        tenant = self.get_object()
        table = AccessoryTable(tenant.accessories.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Accessories'),
            'empty_icon': 'mdi-keyboard-outline',
            'empty_text': _('No accessories found for this tenant.'),
        })

    def get_tab_consumables(self, request):
        tenant = self.get_object()
        table = ConsumableTable(tenant.consumables.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Consumables'),
            'empty_icon': 'mdi-water-outline',
            'empty_text': _('No consumables found for this tenant.'),
        })

    def get_tab_components(self, request):
        tenant = self.get_object()
        from inventory.tables import ComponentTable
        table = ComponentTable(tenant.components.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Component Catalog'),
            'empty_text': _('No components found for this tenant.'),
        })

    def get_tab_custody_policies(self, request):
        tenant = self.get_object()
        from compliance.models import CustodyTemplate
        from compliance.tables import CustodyTemplateTable
        custody_templates_qs = CustodyTemplate.objects.filter(tenant=tenant)
        table = CustodyTemplateTable(custody_templates_qs, request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Custody Policies & EULAs'),
            'empty_text': _('No custody policies found for this tenant.'),
        })

    def get_tab_licenses(self, request):
        tenant = self.get_object()
        table = LicenseTable(tenant.licenses.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Licenses'),
            'empty_icon': 'mdi-key-outline',
            'empty_text': _('No licenses found for this tenant.'),
        })

    def get_tab_kits(self, request):
        tenant = self.get_object()
        table = KitTable(tenant.kits.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Kits'),
            'empty_icon': 'mdi-package-variant',
            'empty_text': _('No kits found for this tenant.'),
        })

    def get_tab_subscriptions(self, request):
        tenant = self.get_object()
        table = SubscriptionTable(tenant.subscriptions_org.all(), request=request)
        table.configure(request)
        return render(request, "generic/tab_table.html", {
            'table': table,
            'title': _('Associated Subscriptions'),
            'empty_icon': 'mdi-file-document-outline',
            'empty_text': _('No subscriptions found for this tenant.'),
        })


class TenantAccessView(LoginRequiredMixin, View):
    """Per-tenant "Who Has Access" audit: lists every user who can reach the tenant,
    the source(s) of their access, and their effective permission count."""

    def get(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        has_access = (
            request.user.is_superuser or
            request.user.has_perm('organization.view_membership', obj=tenant)
        )
        if not has_access:
            raise PermissionDenied(_("You do not have permission to view the access report for this tenant."))
        report = tenant_access_report(tenant)
        return render(request, 'organization/tenants/who_has_access.html', {
            'tenant': tenant,
            'access_report': report,
        })


class TenantManagedTenantsTabView(LoginRequiredMixin, View):
    """Lazy ``hx-get`` partial for the "Managed Tenants" tab on an ``is_provider``
    tenant's detail page (RBAC stage-3 §3).

    Deliberately its own URL (``tenants/<pk>/managed/``) rather than the ``?tab=``
    dispatch ``TenantDetailView``'s sibling tabs use — keeps this tab
    coordinate-free from that view's ``get_tab_*`` method surface.
    """

    def get(self, request, pk):
        # get_object_or_404 goes through Tenant.objects (tenant-scoped): this is
        # correct here, since the tab only ever renders on the PROVIDER tenant's
        # own detail page, which is by definition reachable in the requester's
        # current tenant context.
        tenant = get_object_or_404(Tenant, pk=pk)
        has_access = (
            request.user.is_superuser or
            request.user.has_perm('organization.view_tenant', obj=tenant)
        )
        if not has_access:
            raise PermissionDenied(_("You do not have permission to view this tenant."))
        if not tenant.is_provider:
            raise Http404()

        # _base_manager: managed (customer) tenants are a DIFFERENT tenant than
        # the one active for this request — the scoped default manager would
        # silently return none of them (see "Architecture: tenant scoping").
        managed_tenants = (
            Tenant._base_manager
            .filter(managed_by_id=tenant.pk, deleted_at__isnull=True)
            .select_related('group')
            .annotate(
                member_count=Count(
                    'memberships', filter=Q(memberships__is_active=True), distinct=True,
                ),
            )
            .order_by('name')
        )
        can_add_managed_tenant = (
            request.user.is_superuser or
            request.user.has_perm('organization.add_tenant', obj=tenant)
        )
        return render(request, 'organization/tenants/_tab_managed_tenants.html', {
            'tenant': tenant,
            'managed_tenants': managed_tenants,
            'can_add_managed_tenant': can_add_managed_tenant,
        })


class TenantEditView(ObjectEditView):
    queryset = Tenant.objects.all()
    model = Tenant
    model_form = TenantForm
    template_name = 'generic/object_edit.html'

    def get_form_kwargs(self):
        # ``managed_by_param`` carries "Add managed tenant" (tenants/add/?managed_by=<pk>)
        # through to TenantForm, which forces it server-side for actors holding
        # organization.add_tenant on that MSP tenant (superusers unrestricted;
        # everyone else ignored). request.GET reflects the query string for both
        # GET and POST here since the form posts back to the current URL.
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['managed_by_param'] = self.request.GET.get('managed_by')
        return kwargs


class TenantDeleteView(ObjectDeleteView):
    queryset = Tenant.objects.all()
    model = Tenant
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenant_list')

    def post(self, request, *args, **kwargs):
        tenant = self.get_object()
        related_count = tenant.sites.count() + tenant.locations.count() + tenant.asset_holders.count()

        if related_count > 0:
            related_details = []
            if tenant.sites.exists(): related_details.append(_("%(count)d sites") % {'count': tenant.sites.count()})
            if tenant.locations.exists(): related_details.append(_("%(count)d locations") % {'count': tenant.locations.count()})
            if tenant.asset_holders.exists(): related_details.append(_("%(count)d asset holders") % {'count': tenant.asset_holders.count()})
            messages.error(
                request,
                _("Cannot delete tenant '%(name)s': It is associated with %(details)s.") % {
                    'name': tenant.name,
                    'details': ', '.join(str(d) for d in related_details),
                }
            )
            return redirect(tenant.get_absolute_url())

        return super().post(request, *args, **kwargs)


class TenantBulkEditView(ObjectBulkEditView):
    queryset = Tenant.objects.all()


class TenantBulkDeleteView(ObjectBulkDeleteView):
    queryset = Tenant.objects.all()


@login_required
@require_POST
def tenant_ldap_sync(request, pk):
    from django_q.tasks import async_task
    from django.conf import settings
    from django.db import transaction
    from core.models import Job
    from django.contrib.contenttypes.models import ContentType
    from core.managers import get_current_tenant
    from django.urls import reverse, NoReverseMatch

    tenant = get_object_or_404(Tenant, pk=pk)

    # Security check: verify user has permission to sync LDAP or change tenant
    if not request.user.has_perm('organization.change_tenant'):
        messages.error(request, _("You do not have permission to sync directory settings for this tenant."))
        return redirect(tenant.get_absolute_url())

    tenant_configs = getattr(settings, 'ITAMBOX_TENANT_LDAP_CONFIGS', {})
    if tenant.slug not in tenant_configs:
        messages.error(request, _("No LDAP configuration found for tenant '%(name)s'.") % {'name': tenant.name})
        return redirect(tenant.get_absolute_url())

    ct = ContentType.objects.get_for_model(Tenant)
    current_tenant = get_current_tenant()
    tenant_id = current_tenant.pk if current_tenant else None

    job = Job.objects.create(
        name=f"LDAP Sync: {tenant.name}",
        tenant=current_tenant,
        model=ct,
        status=Job.STATUS_PENDING
    )

    if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
        async_task(
            'core.tasks.sync_tenant_ldap_task',
            job.pk,
            tenant.slug,
            request.user.pk,
            tenant_id
        )
    else:
        transaction.on_commit(
            lambda: async_task(
                'core.tasks.sync_tenant_ldap_task',
                job.pk,
                tenant.slug,
                request.user.pk,
                tenant_id
            )
        )

    messages.success(
        request,
        _("LDAP synchronization job '%(name)s' enqueued successfully! Tracking progress in real-time.") % {'name': job.name}
    )

    try:
        redirect_url = reverse('job_detail', kwargs={'pk': job.pk})
    except NoReverseMatch:
        redirect_url = f"/jobs/{job.pk}/"

    if request.headers.get('HX-Request') or getattr(request, 'htmx', False):
        response = HttpResponse()
        response['HX-Redirect'] = redirect_url
        return response

    return redirect(redirect_url)
