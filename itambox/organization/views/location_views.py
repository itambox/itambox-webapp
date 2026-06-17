from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db.models import Count, Q

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectImportView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.quick_add import QuickAddMixin
from itambox.utils import get_paginate_count
from itambox.panels import Panel
from assets.tables import AssetTable

from ..models import Location
from ..forms import LocationForm, LocationFilterForm
from ..tables import LocationTable
from ..filters import LocationFilterSet
from assets.forms.import_forms import LocationBulkImportForm
from django_tables2 import RequestConfig


class LocationListView(ObjectListView):
    queryset = Location.objects.select_related('site', 'site__region', 'tenant').prefetch_related('tags').annotate(
        asset_count=Count('assets', filter=Q(assets__deleted_at__isnull=True)),
    )
    filterset = LocationFilterSet
    filterset_form = LocationFilterForm
    table = LocationTable
    action_buttons = ('add',)


class LocationDetailView(ObjectDetailView):
    queryset = Location.objects.select_related(
        'site', 'parent', 'tenant'
    ).prefetch_related(
        'children', 'tags', 'assets'
    )

    layout = (
        ((Panel('info', 'Location Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.get_object()

        assets_table = AssetTable(location.assets.all(), request=self.request)
        assets_table.configure(self.request)
        context['assets_table'] = assets_table

        # Accessory Stocks
        from inventory.models import AccessoryStock
        from inventory.tables import AccessoryStockTable
        acc_stock_qs = AccessoryStock.objects.filter(location=location)
        accessory_stocks_table = AccessoryStockTable(acc_stock_qs, request=self.request)
        accessory_stocks_table.configure(self.request)
        context['accessory_stocks_table'] = accessory_stocks_table

        # Consumable Stocks
        from inventory.models import ConsumableStock
        from inventory.tables import ConsumableStockTable
        con_stock_qs = ConsumableStock.objects.filter(location=location)
        consumable_stocks_table = ConsumableStockTable(con_stock_qs, request=self.request)
        consumable_stocks_table.configure(self.request)
        context['consumable_stocks_table'] = consumable_stocks_table

        # Component Stocks
        from inventory.models import ComponentStock
        from inventory.tables import ComponentStockTable
        comp_stock_qs = ComponentStock.objects.filter(location=location)
        component_stocks_table = ComponentStockTable(comp_stock_qs, request=self.request)
        component_stocks_table.configure(self.request)
        context['component_stocks_table'] = component_stocks_table

        # Historical Checkout Log
        from assets.models import AssetAssignment
        from organization.tables import AssetAssignmentTable
        asset_assignments_qs = AssetAssignment.objects.filter(assigned_location=location)
        asset_assignments_table = AssetAssignmentTable(asset_assignments_qs, request=self.request)
        asset_assignments_table.configure(self.request)
        context['asset_assignments_table'] = asset_assignments_table

        # Audit Campaigns
        from compliance.models import AuditSession
        from compliance.views_audit import AuditSessionTable
        audits_qs = AuditSession.objects.filter(location=location)
        audits_table = AuditSessionTable(audits_qs, request=self.request)
        audits_table.configure(self.request)
        context['audits_table'] = audits_table

        related_objects_list = []
        asset_count = location.assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?location={location.slug}"
            })
        child_count = location.children.count()
        if child_count:
            related_objects_list.append({
                'label': 'Child Locations',
                'count': child_count,
                'url': f"{reverse('organization:location_list')}?parent={location.slug}"
            })
        accessory_count = acc_stock_qs.count()
        if accessory_count:
            related_objects_list.append({
                'label': 'Accessory Stocks',
                'count': accessory_count,
                'url': f"{reverse('inventory:accessorystock_list')}?location={location.slug}"
            })
        consumable_count = con_stock_qs.count()
        if consumable_count:
            related_objects_list.append({
                'label': 'Consumable Stocks',
                'count': consumable_count,
                'url': f"{reverse('inventory:consumablestock_list')}?location={location.slug}"
            })
        component_count = comp_stock_qs.count()
        if component_count:
            related_objects_list.append({
                'label': 'Component Stocks',
                'count': component_count,
                'url': f"{reverse('inventory:componentstock_list')}?location={location.slug}"
            })
        checkout_count = asset_assignments_qs.count()
        if checkout_count:
            related_objects_list.append({
                'label': 'Checkout Log',
                'count': checkout_count,
                'url': f"{reverse('organization:location_detail', kwargs={'pk': location.pk})}#checkout-log"
            })
        audit_count = audits_qs.count()
        if audit_count:
            related_objects_list.append({
                'label': 'Audit Campaigns',
                'count': audit_count,
                'url': f"{reverse('compliance:auditsession_list')}?location={location.slug}"
            })

        context['related_objects_list'] = related_objects_list
        return context



class LocationEditView(QuickAddMixin, ObjectEditView):
    queryset = Location.objects.all()
    model = Location
    model_form = LocationForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_location'


class LocationCloneView(ObjectCloneView):
    model = Location
    model_form = LocationForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'organization:location_list'


class LocationDeleteView(ObjectDeleteView):
    queryset = Location.objects.all()
    model = Location
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:location_list')

    def post(self, request, *args, **kwargs):
        location = self.get_object()
        asset_count = location.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete location '{location.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(location.get_absolute_url())

        return super().post(request, *args, **kwargs)


class LocationBulkEditView(ObjectBulkEditView):
    queryset = Location.objects.all()


class LocationBulkDeleteView(ObjectBulkDeleteView):
    queryset = Location.objects.all()
