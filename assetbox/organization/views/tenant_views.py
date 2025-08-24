from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db.models import Count

from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assets.tables import AssetTable, AccessoryTable, ConsumableTable, KitTable
from licenses.tables import LicenseTable
from subscriptions.tables import SubscriptionTable

from ..models import Tenant
from ..forms import TenantForm, TenantFilterForm
from ..tables import TenantTable, SiteTable, LocationTable, AssetHolderTable
from ..filters import TenantFilterSet
from django_tables2 import RequestConfig


class TenantListView(ObjectListView):
    queryset = Tenant.objects.select_related('group').prefetch_related('tags').annotate(
        site_count=Count('sites'),
        location_count=Count('locations'),
    )
    filterset = TenantFilterSet
    filterset_form = TenantFilterForm
    table = TenantTable
    action_buttons = ('add',)


class TenantDetailView(ObjectDetailView):
    queryset = Tenant.objects.select_related('group').prefetch_related(
        'tags', 'sites__region', 'locations__site'
    )

    layout = (
        ((Panel('info', 'Tenant Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.get_object()

        sites_table = SiteTable(tenant.sites.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(sites_table)
        context['sites_table'] = sites_table

        locations_table = LocationTable(tenant.locations.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(locations_table)
        context['locations_table'] = locations_table

        assetholders_table = AssetHolderTable(tenant.asset_holders.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assetholders_table)
        context['assetholders_table'] = assetholders_table

        assets_table = AssetTable(tenant.assets.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)
        context['assets_table'] = assets_table

        accessories_table = AccessoryTable(tenant.accessories.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(accessories_table)
        context['accessories_table'] = accessories_table

        consumables_table = ConsumableTable(tenant.consumables.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(consumables_table)
        context['consumables_table'] = consumables_table

        licenses_table = LicenseTable(tenant.licenses.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(licenses_table)
        context['licenses_table'] = licenses_table

        kits_table = KitTable(tenant.kits.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(kits_table)
        context['kits_table'] = kits_table

        subscriptions_table = SubscriptionTable(tenant.subscriptions_org.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(subscriptions_table)
        context['subscriptions_table'] = subscriptions_table

        context['tenant_asset_count'] = tenant.assets.count()
        context['tenant_accessory_count'] = tenant.accessories.count()
        context['tenant_consumable_count'] = tenant.consumables.count()
        context['tenant_license_count'] = tenant.licenses.count()
        context['tenant_kit_count'] = tenant.kits.count()
        context['tenant_subscription_count'] = tenant.subscriptions_org.count()
        return context


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
            if tenant.sites.exists(): related_details.append(f"{tenant.sites.count()} sites")
            if tenant.locations.exists(): related_details.append(f"{tenant.locations.count()} locations")
            if tenant.asset_holders.exists(): related_details.append(f"{tenant.asset_holders.count()} asset holders")
            messages.error(
                request,
                f"Cannot delete tenant '{tenant.name}': It is associated with {', '.join(related_details)}."
            )
            return redirect(tenant.get_absolute_url())

        return super().post(request, *args, **kwargs)


class TenantBulkEditView(ObjectBulkEditView):
    queryset = Tenant.objects.all()


class TenantBulkDeleteView(ObjectBulkDeleteView):
    queryset = Tenant.objects.all()
