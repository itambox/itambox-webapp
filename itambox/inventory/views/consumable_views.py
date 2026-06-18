import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.http import HttpResponse
from django.views.generic import View
from django.utils.translation import gettext_lazy as _

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from itambox.views.generic.service_views import GenericTransactionView
from itambox.panels import Panel

from ..models import Consumable, Kit, ConsumableStock, ConsumableAssignment
from .. import forms, tables, filters
from inventory.services import checkout_inventory_item


class ConsumableListView(ObjectListView):
    queryset = Consumable.objects.with_counts().select_related('tenant', 'manufacturer').prefetch_related('tags')
    filterset = filters.ConsumableFilterSet
    filterset_form = forms.ConsumableFilterForm
    table = tables.ConsumableTable
    action_buttons = ('add',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Consumables')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (None, _('Inventory & Stock')),
            (None, _('Consumables'))
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from organization.models import AssetHolder, Location
            from assets.models import Asset
            context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
            context['locations'] = Location.objects.all().order_by('name')
            context['assets'] = Asset.objects.all().order_by('asset_tag')
        return context


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
                _("Cannot delete consumable '%(consumable)s': It has %(count)s historical consumption records.") % {"consumable": consumable, "count": consumption_count}
            )
            return redirect(consumable.get_absolute_url())
        return super().post(request, *args, **kwargs)


class ConsumableCloneView(ObjectCloneView):
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'inventory:consumable_list'


class ConsumableCheckoutView(GenericTransactionView):
    queryset = Consumable.objects.all()
    model_form = forms.ConsumableCheckoutForm
    service_callable = checkout_inventory_item
    context_object_name = 'consumable'
    template_name = 'inventory/includes/consumable_checkout_modal.html'
    error_partial = 'inventory/includes/consumable_checkout_modal.html#checkout-modal-form'
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
        if 'initial' not in kwargs:
            kwargs['initial'] = {}
        for key in self.request.GET:
            kwargs['initial'][key] = self.request.GET.get(key)
        return kwargs


class ConsumableBulkEditView(ObjectBulkEditView):
    queryset = Consumable.objects.all()


class ConsumableBulkDeleteView(ObjectBulkDeleteView):
    queryset = Consumable.objects.all()


class ConsumableStockListView(ObjectListView):
    queryset = ConsumableStock.objects.select_related('consumable', 'location').all()
    table = tables.ConsumableStockTable
    action_buttons = ('add',)
    filterset = filters.ConsumableStockFilterSet
    filterset_form = forms.ConsumableStockFilterForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Consumable Stocks')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('inventory:consumable_list'), _('Consumables')),
            (None, _('Stocks'))
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from organization.models import AssetHolder, Location
            from assets.models import Asset
            context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
            context['locations'] = Location.objects.all().order_by('name')
            context['assets'] = Asset.objects.all().order_by('asset_tag')
        return context



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


class ConsumableAssignmentListView(ObjectListView):
    queryset = ConsumableAssignment.objects.select_related(
        'consumable', 'assigned_holder', 'assigned_location', 'assigned_asset'
    ).all()
    table = tables.ConsumableAssignmentTable
    action_buttons = ()
    filterset = filters.ConsumableAssignmentFilterSet
    filterset_form = forms.ConsumableAssignmentFilterForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Consumable Consumptions')
        context['breadcrumbs'] = [
            (reverse('dashboard'), _('Dashboard')),
            (reverse('inventory:consumable_list'), _('Consumables')),
            (None, _('Consumptions'))
        ]
        return context


class ConsumableStockAdjustView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.http import HttpResponse, HttpResponseForbidden
        from django.utils.html import format_html

        stock = get_object_or_404(ConsumableStock, pk=pk)
        if not request.user.has_perm('inventory.change_consumablestock', obj=stock.consumable):
            return HttpResponseForbidden(_("Permission denied."))
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


class ConsumableStockCreateModalView(LoginRequiredMixin, View):
    def get(self, request, pk):
        consumable = get_object_or_404(Consumable, pk=pk)
        from ..forms import ConsumableStockModalForm
        initial = {}
        location_id = request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        form = ConsumableStockModalForm(initial=initial)
        return render(request, 'generic/includes/add_stock_modal.html', {
            'object': consumable,
            'form': form,
            'post_url': reverse('inventory:consumable_add_stock', kwargs={'pk': consumable.pk}),
        })

    def post(self, request, pk):
        consumable = get_object_or_404(Consumable, pk=pk)
        from ..forms import ConsumableStockModalForm
        form = ConsumableStockModalForm(request.POST)
        if form.is_valid():
            stock = form.save(commit=False)
            stock.consumable = consumable
            stock.save()
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "tableRefreshRequired": None,
                    "showMessage": {
                        "message": str(_("Added stock pool for %(location)s.") % {"location": stock.location}),
                        "level": "success"
                    }
                })
                return response
            return redirect(consumable.get_absolute_url())

        return render(request, 'generic/includes/add_stock_modal.html', {
            'object': consumable,
            'form': form,
            'post_url': reverse('inventory:consumable_add_stock', kwargs={'pk': consumable.pk}),
        })
