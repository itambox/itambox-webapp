from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig

from ..models import Supplier, Asset
from .. import forms, tables, filters

from itambox.utils import get_paginate_count
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectCloneView,
)


class SupplierListView(ObjectListView):
    queryset = Supplier.objects.prefetch_related('tags')
    filterset = filters.SupplierFilterSet
    filterset_form = forms.SupplierFilterForm
    table = tables.SupplierTable
    action_buttons = ("add",)


class SupplierDetailView(ObjectDetailView):
    queryset = Supplier.objects.all()

    layout = (
        ((Panel('info', _('Supplier Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier = self.get_object()

        supplier_assets = Asset.objects.filter(supplier=supplier).select_related(
            'asset_role', 'asset_type', 'location'
        )
        assets_table = tables.AssetTable(supplier_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)
        context['assets_table'] = assets_table

        # Accessories Supplied
        from inventory.models import Accessory
        from inventory.tables import AccessoryTable
        accessory_qs = Accessory.objects.filter(supplier=supplier).select_related('manufacturer', 'category', 'tenant').prefetch_related('tags')
        accessories_table = AccessoryTable(accessory_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(accessories_table)
        context['accessories_table'] = accessories_table

        # Maintenance Expenditures
        from assets.models import AssetMaintenance
        from assets.tables import AssetMaintenanceTable
        maintenance_qs = AssetMaintenance.objects.filter(supplier=supplier).select_related('asset', 'supplier')
        maintenances_table = AssetMaintenanceTable(maintenance_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(maintenances_table)
        context['maintenances_table'] = maintenances_table

        # Licenses Purchased
        from licenses.models import License
        from licenses.tables import LicenseTable
        license_qs = License.objects.filter(supplier=supplier).select_related('software', 'tenant').prefetch_related('tags')
        licenses_table = LicenseTable(license_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(licenses_table)
        context['licenses_table'] = licenses_table

        related_objects_list = []
        asset_count = supplier_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?supplier={supplier.slug}"
            })
        accessory_count = accessory_qs.count()
        if accessory_count:
            related_objects_list.append({
                'label': 'Accessories',
                'count': accessory_count,
                'url': f"{reverse('inventory:accessory_list')}?supplier={supplier.slug}"
            })
        maintenance_count = maintenance_qs.count()
        if maintenance_count:
            related_objects_list.append({
                'label': 'Maintenances',
                'count': maintenance_count,
                'url': f"{reverse('assets:assetmaintenance_list')}?supplier={supplier.slug}"
            })
        license_count = license_qs.count()
        if license_count:
            related_objects_list.append({
                'label': 'Licenses',
                'count': license_count,
                'url': f"{reverse('licenses:license_list')}?supplier={supplier.slug}"
            })
        context['related_objects_list'] = related_objects_list
        return context



class SupplierEditView(ObjectEditView):
    queryset = Supplier.objects.all()
    model = Supplier
    model_form = forms.SupplierForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:supplier_list"


class SupplierDeleteView(ObjectDeleteView):
    queryset = Supplier.objects.all()
    model = Supplier
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:supplier_list")


class SupplierCloneView(ObjectCloneView):
    model = Supplier
    model_form = forms.SupplierForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:supplier_list'
