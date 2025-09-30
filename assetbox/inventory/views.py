import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.generic import View
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from django_tables2 import RequestConfig

# Core generic views & helpers
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectImportView
)
from assetbox.views.generic.service_views import GenericTransactionView, SimplePostView
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel

# Assets bulk import forms
from assets.forms.import_forms import AccessoryBulkImportForm, ConsumableBulkImportForm

# Inventory models, forms, tables, filters
from .models import Accessory, Consumable, Kit, KitItem, AccessoryStock, ConsumableStock, AccessoryAssignment
from . import forms, tables, filters
from assets.models import Asset
from assets.services import checkout_accessory, checkin_accessory, checkout_consumable, checkout_kit


class AccessoryListView(ObjectListView):
    queryset = Accessory.objects.select_related('tenant', 'manufacturer').prefetch_related('tags').annotate(_total_stock=Coalesce(Sum('stocks__qty'), 0), _checked_out=Coalesce(Sum('assignments__qty'), 0))
    filterset = filters.AccessoryFilterSet
    filterset_form = forms.AccessoryFilterForm
    table = tables.AccessoryTable
    action_buttons = ('add',)


class AccessoryDetailView(ObjectDetailView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags', 'assignments__assigned_holder', 'assignments__assigned_location', 'stocks__location')
    template_name = 'assets/accessories/accessory_detail.html'

    layout = (
        ((Panel('metrics', 'Metrics Overview'),),),
        ((Panel('info', 'Accessory Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accessory = self.get_object()

        assignments_table = tables.AccessoryAssignmentTable(accessory.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)
        context['assignments_table'] = assignments_table

        stocks_table = tables.AccessoryStockTable(accessory.stocks.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(stocks_table)
        context['stocks_table'] = stocks_table
        return context


class AccessoryEditView(ObjectEditView):
    queryset = Accessory.objects.all()
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class AccessoryDeleteView(ObjectDeleteView):
    queryset = Accessory.objects.all()
    model = Accessory
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:accessory_list')

    def post(self, request, *args, **kwargs):
        accessory = self.get_object()
        assignment_count = accessory.assignments.count()
        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete accessory '{accessory}': It has {assignment_count} active assignments."
            )
            return redirect(accessory.get_absolute_url())
        return super().post(request, *args, **kwargs)


class AccessoryCloneView(ObjectCloneView):
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class ConsumableListView(ObjectListView):
    queryset = Consumable.objects.select_related('tenant', 'manufacturer').prefetch_related('tags').annotate(_total_stock=Coalesce(Sum('stocks__qty'), 0), _consumed=Coalesce(Sum('consumptions__qty'), 0))
    filterset = filters.ConsumableFilterSet
    filterset_form = forms.ConsumableFilterForm
    table = tables.ConsumableTable
    action_buttons = ('add',)


class ConsumableDetailView(ObjectDetailView):
    queryset = Consumable.objects.select_related('manufacturer').prefetch_related('tags', 'consumptions__assigned_holder', 'consumptions__assigned_location', 'stocks__location')
    template_name = 'assets/consumables/consumable_detail.html'

    layout = (
        ((Panel('metrics', 'Metrics Overview'),),),
        ((Panel('info', 'Consumable Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        consumable = self.get_object()

        consumptions_table = tables.ConsumableAssignmentTable(consumable.consumptions.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(consumptions_table)
        context['consumptions_table'] = consumptions_table

        stocks_table = tables.ConsumableStockTable(consumable.stocks.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(stocks_table)
        context['stocks_table'] = stocks_table
        return context


class ConsumableEditView(ObjectEditView):
    queryset = Consumable.objects.all()
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:consumable_list'


class ConsumableDeleteView(ObjectDeleteView):
    queryset = Consumable.objects.all()
    model = Consumable
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:consumable_list')

    def post(self, request, *args, **kwargs):
        consumable = self.get_object()
        consumption_count = consumable.consumptions.count()
        if consumption_count > 0:
            messages.error(
                request,
                f"Cannot delete consumable '{consumable}': It has {consumption_count} historical consumption records."
            )
            return redirect(consumable.get_absolute_url())
        return super().post(request, *args, **kwargs)


class ConsumableCloneView(ObjectCloneView):
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:consumable_list'


class KitListView(ObjectListView):
    queryset = Kit.objects.select_related('tenant').annotate(item_count=Count('items'))
    filterset = filters.KitFilterSet
    filterset_form = forms.KitFilterForm
    table = tables.KitTable
    action_buttons = ('add',)


class KitDetailView(ObjectDetailView):
    queryset = Kit.objects.all().prefetch_related('items__asset_type', 'items__accessory', 'items__license__software', 'items__consumable')
    template_name = 'assets/kits/kit_detail.html'

    layout = (
        ((Panel('info', 'Kit Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check availability of each kit item
        items_with_availability = []
        all_available = True
        
        for item in self.object.items.all():
            avail = 0
            if item.asset_type:
                avail = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').count()
                if avail < 1:
                    all_available = False
            elif item.accessory:
                avail = item.accessory.available
                if avail < item.qty:
                    all_available = False
            elif item.license:
                avail = item.license.available_seats
                if avail < 1:
                    all_available = False
            elif item.consumable:
                avail = item.consumable.available
                if avail < item.qty:
                    all_available = False
            
            items_with_availability.append({
                'item': item,
                'available_count': avail,
                'is_available': (avail >= (item.qty if (item.accessory or item.consumable) else 1))
            })
            
        context['items_with_availability'] = items_with_availability
        context['all_available'] = all_available
        return context


class KitEditView(ObjectEditView):
    queryset = Kit.objects.all()
    model = Kit
    model_form = forms.KitForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:kit_list'


class KitDeleteView(ObjectDeleteView):
    queryset = Kit.objects.all()
    model = Kit
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:kit_list')


class KitItemEditView(ObjectEditView):
    queryset = KitItem.objects.all()
    model = KitItem
    model_form = forms.KitItemForm
    template_name = 'generic/object_edit.html'

    def get_initial(self):
        initial = super().get_initial()
        kit_id = self.request.GET.get('kit')
        if kit_id:
            initial['kit'] = kit_id
        return initial

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('inventory:kit_list')


class KitItemDeleteView(ObjectDeleteView):
    queryset = KitItem.objects.all()
    model = KitItem
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('inventory:kit_list')


class KitCheckoutView(GenericTransactionView):
    queryset = Kit.objects.all()
    model_form = forms.KitCheckoutForm
    service_callable = checkout_kit
    context_object_name = 'kit'
    template_name = 'assets/includes/kit_checkout_modal.html'
    success_message = "Kit checked out successfully."
    hx_trigger = "kitListUpdated"
    form_field_map = {
        'assigned_holder': 'holder',
        'assigned_location': 'location',
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        return kwargs


class AccessoryCheckoutView(GenericTransactionView):
    queryset = Accessory.objects.all()
    model_form = forms.AccessoryCheckoutForm
    service_callable = checkout_accessory
    context_object_name = 'accessory'
    template_name = 'assets/includes/accessory_checkout_modal.html'
    success_message = "Accessory checked out successfully."
    form_field_map = {
        'assigned_holder': 'holder',
        'assigned_location': 'location',
        'assigned_asset': 'asset',
        'from_location': 'source_location',
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['accessory'] = self.get_object()
        return kwargs


class AccessoryCheckinView(SimplePostView):
    queryset = AccessoryAssignment.objects.all()

    def perform_action(self, assignment, request):
        accessory, qty, recipient = checkin_accessory(assignment.pk, user=request.user)
        return {
            'message': f"Checked in {qty}x '{accessory}' from {recipient}.",
            'redirect': accessory.get_absolute_url(),
        }

    def get_success_redirect(self, obj, result):
        return redirect(result.get('redirect') or '/')


class ConsumableCheckoutView(GenericTransactionView):
    queryset = Consumable.objects.all()
    model_form = forms.ConsumableCheckoutForm
    service_callable = checkout_consumable
    context_object_name = 'consumable'
    template_name = 'assets/includes/consumable_checkout_modal.html'
    success_message = "Consumable consumed successfully."
    form_field_map = {
        'assigned_holder': 'holder',
        'assigned_location': 'location',
        'assigned_asset': 'asset',
        'from_location': 'source_location',
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['consumable'] = self.get_object()
        return kwargs


class AccessoryImportView(ObjectImportView):
    model_form = AccessoryBulkImportForm


class ConsumableImportView(ObjectImportView):
    model_form = ConsumableBulkImportForm


class AccessoryBulkEditView(ObjectBulkEditView):
    queryset = Accessory.objects.all()


class AccessoryBulkDeleteView(ObjectBulkDeleteView):
    queryset = Accessory.objects.all()


class ConsumableBulkEditView(ObjectBulkEditView):
    queryset = Consumable.objects.all()


class ConsumableBulkDeleteView(ObjectBulkDeleteView):
    queryset = Consumable.objects.all()


class AccessoryStockListView(ObjectListView):
    queryset = AccessoryStock.objects.select_related('accessory', 'location').all()
    table = tables.AccessoryStockTable
    action_buttons = ('add',)
    filterset = filters.AccessoryStockFilterSet
    filterset_form = forms.AccessoryStockFilterForm


class AccessoryStockEditView(ObjectEditView):
    queryset = AccessoryStock.objects.all()
    model = AccessoryStock
    model_form = forms.AccessoryStockForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:accessory_list'


class AccessoryStockDeleteView(ObjectDeleteView):
    queryset = AccessoryStock.objects.all()
    model = AccessoryStock
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:accessory_list')


class ConsumableStockListView(ObjectListView):
    queryset = ConsumableStock.objects.select_related('consumable', 'location').all()
    table = tables.ConsumableStockTable
    action_buttons = ('add',)
    filterset = filters.ConsumableStockFilterSet
    filterset_form = forms.ConsumableStockFilterForm



class ConsumableStockEditView(ObjectEditView):
    queryset = ConsumableStock.objects.all()
    model = ConsumableStock
    model_form = forms.ConsumableStockForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:consumable_list'


class ConsumableStockDeleteView(ObjectDeleteView):
    queryset = ConsumableStock.objects.all()
    model = ConsumableStock
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('inventory:consumable_list')
