from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
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

from ..models import Tenant, Provider
from ..forms import TenantForm, TenantFilterForm
from ..tables import TenantTable, SiteTable, LocationTable, AssetHolderTable
from ..filters import TenantFilterSet
from ..access import tenant_access_report
from django_tables2 import RequestConfig


class TenantListView(ObjectListView):
    queryset = Tenant.objects.select_related('group', 'provider').prefetch_related('tags').annotate(
        site_count=Count('sites', distinct=True),
        location_count=Count('locations', distinct=True),
    )
    filterset = TenantFilterSet
    filterset_form = TenantFilterForm
    table = TenantTable
    action_buttons = ('add',)

    def _manageable_provider_ids(self):
        """Provider PKs the requesting user may administer, or ``None`` meaning *all*.

        Superuser → ``None`` (every provider). Otherwise the set of providers the user
        holds ``organization.manage_provider`` against — never "any provider grants all",
        so a single-provider admin can only ever see their OWN provider's tenants.
        """
        user = self.request.user
        if not (user and user.is_authenticated):
            return set()
        if user.is_superuser:
            return None
        return {
            p.pk for p in Provider._base_manager.filter(deleted_at__isnull=True)
            if user.has_perm('organization.manage_provider', obj=p)
        }

    def _can_view_all_providers(self):
        """Whether the requesting user may opt into the cross-provider tenant set.

        True for a superuser, or a user holding ``organization.manage_provider`` against
        at least one Provider. The *scope* of what they then see is still restricted to
        their own managed providers (see ``get_queryset``). Anyone else stays tenant-scoped.
        """
        ids = self._manageable_provider_ids()
        return ids is None or bool(ids)

    def get_queryset(self):
        """Default: tenant-scoped (``Tenant.objects``) — never widened for ordinary users.

        A provider-admin may explicitly opt into the cross-provider set of tenants they
        manage with ``?all_providers=true``; the toggle is honoured ONLY for providers the
        user actually holds ``manage_provider`` on (a superuser sees every provider-managed
        tenant). This preserves the MSP cross-tenant capability the removed
        ``CustomerTenantListView`` provided, without a separate route, without loosening
        isolation for everyone else, and WITHOUT leaking other MSPs' tenants to a
        single-provider admin.
        """
        if self.request.GET.get('all_providers') == 'true':
            managed = self._manageable_provider_ids()
            if managed is None:
                # Superuser: every provider-managed, non-deleted tenant.
                base = Tenant._base_manager.filter(
                    provider__isnull=False, deleted_at__isnull=True,
                )
            elif managed:
                # Provider admin: ONLY tenants under the providers they manage.
                base = Tenant._base_manager.filter(
                    provider_id__in=managed, deleted_at__isnull=True,
                )
            else:
                base = None
            if base is not None:
                # _base_manager bypasses tenant scoping (a provider admin legitimately
                # views across their own customer tenants).
                self.queryset = (
                    base
                    .select_related('group', 'provider')
                    .prefetch_related('tags')
                    .annotate(
                        site_count=Count('sites', distinct=True),
                        location_count=Count('locations', distinct=True),
                    )
                )
        return super().get_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_view_all_providers'] = self._can_view_all_providers()
        context['viewing_all_providers'] = (
            self.request.GET.get('all_providers') == 'true'
            and context['can_view_all_providers']
        )
        return context


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
            request.user.has_perm('organization.view_membership', obj=tenant) or
            request.user.has_perm('organization.change_tenant', obj=tenant) or
            request.user.has_perm('organization.manage_staff', obj=tenant)
        )
        if not has_access:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied(_("You do not have permission to view the access report for this tenant."))
        report = tenant_access_report(tenant)
        return render(request, 'organization/tenants/who_has_access.html', {
            'tenant': tenant,
            'access_report': report,
        })


class TenantEditView(ObjectEditView):
    queryset = Tenant.objects.all()
    model = Tenant
    model_form = TenantForm
    template_name = 'generic/object_edit.html'


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
