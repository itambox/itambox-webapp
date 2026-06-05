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
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectImportView
)
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView
from itambox.utils import get_paginate_count
from itambox.panels import Panel

# Assets bulk import forms
from assets.forms.import_forms import AccessoryBulkImportForm, ConsumableBulkImportForm

# Inventory models, forms, tables, filters
from .models import Accessory, Consumable, Kit, KitItem, AccessoryStock, ConsumableStock, AccessoryAssignment, ConsumableAssignment
from . import forms, tables, filters
from assets.models import Asset
from assets.services import checkout_kit
from inventory.services import checkout_accessory, checkin_accessory, checkout_consumable


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
        assignments_table.configure(self.request)
        context['assignments_table'] = assignments_table

        stocks_table = tables.AccessoryStockTable(accessory.stocks.all(), request=self.request)
        stocks_table.configure(self.request)
        context['stocks_table'] = stocks_table

        # Kits
        kits_qs = Kit.objects.filter(items__accessory=accessory).distinct()
        kits_table = tables.KitTable(kits_qs, request=self.request)
        kits_table.configure(self.request)
        context['kits_table'] = kits_table

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
        consumptions_table.configure(self.request)
        context['consumptions_table'] = consumptions_table

        stocks_table = tables.ConsumableStockTable(consumable.stocks.all(), request=self.request)
        stocks_table.configure(self.request)
        context['stocks_table'] = stocks_table

        # Kits
        kits_qs = Kit.objects.filter(items__consumable=consumable).distinct()
        kits_table = tables.KitTable(kits_qs, request=self.request)
        kits_table.configure(self.request)
        context['kits_table'] = kits_table

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
        
        items = list(self.object.items.all())
        asset_type_ids = [i.asset_type_id for i in items if i.asset_type_id]
        accessory_ids = [i.accessory_id for i in items if i.accessory_id]
        license_ids = [i.license_id for i in items if i.license_id]
        consumable_ids = [i.consumable_id for i in items if i.consumable_id]

        # 1. Batch Asset Availability Count
        asset_counts = {}
        if asset_type_ids:
            from django.db.models import Count
            counts = Asset.objects.filter(
                asset_type_id__in=asset_type_ids,
                status__slug='available'
            ).values('asset_type_id').annotate(count=Count('id'))
            asset_counts = {c['asset_type_id']: c['count'] for c in counts}

        # 2. Batch Accessory Available Qty
        accessory_avail = {}
        if accessory_ids:
            from django.db.models import Sum, Q
            stocks = Accessory.objects.filter(id__in=accessory_ids).annotate(
                total_qty=Coalesce(Sum('stocks__qty'), 0),
                undeducted_qty=Coalesce(Sum('assignments__qty', filter=Q(assignments__from_location__isnull=True)), 0)
            ).values('id', 'total_qty', 'undeducted_qty')
            for s in stocks:
                accessory_avail[s['id']] = max(0, s['total_qty'] - s['undeducted_qty'])

        # 3. Batch License Available Seats
        license_avail = {}
        if license_ids:
            from django.db.models import Count
            from licenses.models import License
            licenses = License.objects.filter(id__in=license_ids).annotate(
                assigned_count=Count('assignments')
            ).values('id', 'seats', 'assigned_count')
            for l in licenses:
                license_avail[l['id']] = max(0, l['seats'] - l['assigned_count'])

        # 4. Batch Consumable Available Qty
        consumable_avail = {}
        if consumable_ids:
            from django.db.models import Sum, Q
            stocks = Consumable.objects.filter(id__in=consumable_ids).annotate(
                total_qty=Coalesce(Sum('stocks__qty'), 0),
                undeducted_qty=Coalesce(Sum('consumptions__qty', filter=Q(consumptions__from_location__isnull=True)), 0)
            ).values('id', 'total_qty', 'undeducted_qty')
            for s in stocks:
                consumable_avail[s['id']] = max(0, s['total_qty'] - s['undeducted_qty'])

        for item in items:
            avail = 0
            if item.asset_type_id:
                avail = asset_counts.get(item.asset_type_id, 0)
                if avail < 1:
                    all_available = False
            elif item.accessory_id:
                avail = accessory_avail.get(item.accessory_id, 0)
                if avail < item.qty:
                    all_available = False
            elif item.license_id:
                avail = license_avail.get(item.license_id, 0)
                if avail < 1:
                    all_available = False
            elif item.consumable_id:
                avail = consumable_avail.get(item.consumable_id, 0)
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
        kwargs['kit'] = self.get_object()
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


class AccessoryAssignmentListView(ObjectListView):
    queryset = AccessoryAssignment.objects.select_related(
        'accessory', 'assigned_holder', 'assigned_location', 'assigned_asset'
    ).all()
    table = tables.AccessoryAssignmentTable
    action_buttons = ()
    filterset = filters.AccessoryAssignmentFilterSet
    filterset_form = forms.AccessoryAssignmentFilterForm


class ConsumableAssignmentListView(ObjectListView):
    queryset = ConsumableAssignment.objects.select_related(
        'consumable', 'assigned_holder', 'assigned_location', 'assigned_asset'
    ).all()
    table = tables.ConsumableAssignmentTable
    action_buttons = ()
    filterset = filters.ConsumableAssignmentFilterSet
    filterset_form = forms.ConsumableAssignmentFilterForm


class AccessoryStockAdjustView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.http import HttpResponse, HttpResponseForbidden
        from django.utils.html import format_html
        
        stock = get_object_or_404(AccessoryStock, pk=pk)
        if not request.user.has_perm('inventory.change_accessorystock', obj=stock.accessory):
            return HttpResponseForbidden("Permission denied.")
        action = request.GET.get('action')
        
        if action == 'increment':
            stock.qty += 1
            stock.save()
        elif action == 'decrement':
            if stock.qty > 0:
                stock.qty -= 1
                stock.save()
                
        return HttpResponse(format_html(
            '<div class="d-flex align-items-center justify-content-start">'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary me-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-minus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '  <span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary ms-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-plus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '</div>',
            reverse('inventory:accessorystock_adjust', kwargs={'pk': stock.pk}) + '?action=decrement',
            stock.qty,
            reverse('inventory:accessorystock_adjust', kwargs={'pk': stock.pk}) + '?action=increment'
        ))


class ConsumableStockAdjustView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.http import HttpResponse, HttpResponseForbidden
        from django.utils.html import format_html
        
        stock = get_object_or_404(ConsumableStock, pk=pk)
        if not request.user.has_perm('inventory.change_consumablestock', obj=stock.consumable):
            return HttpResponseForbidden("Permission denied.")
        action = request.GET.get('action')
        
        if action == 'increment':
            stock.qty += 1
            stock.save()
        elif action == 'decrement':
            if stock.qty > 0:
                stock.qty -= 1
                stock.save()
                
        return HttpResponse(format_html(
            '<div class="d-flex align-items-center justify-content-start">'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary me-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-minus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '  <span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary ms-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-plus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '</div>',
            reverse('inventory:consumablestock_adjust', kwargs={'pk': stock.pk}) + '?action=decrement',
            stock.qty,
            reverse('inventory:consumablestock_adjust', kwargs={'pk': stock.pk}) + '?action=increment'
        ))


