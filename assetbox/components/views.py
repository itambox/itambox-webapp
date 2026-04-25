from django.urls import reverse_lazy
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2 import RequestConfig
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectCloneView
)
from .models import Component, ComponentStock, ComponentAllocation
from .forms import (
    ComponentForm, ComponentStockForm, ComponentAllocationForm,
    ComponentFilterForm, ComponentStockFilterForm, ComponentAllocationFilterForm,
)
from .filters import ComponentFilterSet, ComponentStockFilterSet, ComponentAllocationFilterSet
from .tables import ComponentTable, ComponentStockTable, ComponentAllocationTable


# =============================================================================
# Component Views (new quantity-based system)
# =============================================================================

class ComponentListView(ObjectListView):
    queryset = Component.objects.with_counts().select_related('manufacturer', 'category').prefetch_related('tags')
    filterset = ComponentFilterSet
    filterset_form = ComponentFilterForm
    table = ComponentTable
    action_buttons = ('add',)


class ComponentDetailView(ObjectDetailView):
    queryset = Component.objects.select_related('manufacturer', 'category').prefetch_related('tags', 'stocks', 'allocations')

    layout = (
        ((Panel('metrics', 'Metrics Overview'),),),
        ((Panel('info', 'Component Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        component = self.get_object()

        stocks_table = ComponentStockTable(component.stocks.all(), request=self.request)
        stocks_table.configure(self.request)
        context['stocks_table'] = stocks_table

        allocations_table = ComponentAllocationTable(
            component.allocations.filter(deleted_at__isnull=True),
            request=self.request
        )
        allocations_table.configure(self.request)
        context['allocations_table'] = allocations_table

        return context


class ComponentEditView(ObjectEditView):
    queryset = Component.objects.all()
    model = Component
    model_form = ComponentForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'components:component_list'


class ComponentDeleteView(ObjectDeleteView):
    queryset = Component.objects.all()
    model = Component
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('components:component_list')


class ComponentCloneView(ObjectCloneView):
    model = Component
    model_form = ComponentForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'components:component_list'


class ComponentStockListView(ObjectListView):
    queryset = ComponentStock.objects.select_related('component', 'location')
    filterset = ComponentStockFilterSet
    filterset_form = ComponentStockFilterForm
    table = ComponentStockTable
    action_buttons = ('add',)


class ComponentStockEditView(ObjectEditView):
    queryset = ComponentStock.objects.all()
    model = ComponentStock
    model_form = ComponentStockForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'components:componentstock_list'


class ComponentStockDeleteView(ObjectDeleteView):
    queryset = ComponentStock.objects.all()
    model = ComponentStock
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('components:componentstock_list')


class ComponentAllocationListView(ObjectListView):
    queryset = ComponentAllocation.objects.select_related('component', 'asset').prefetch_related('tags')
    filterset = ComponentAllocationFilterSet
    filterset_form = ComponentAllocationFilterForm
    table = ComponentAllocationTable
    action_buttons = ('add',)


class ComponentAllocationEditView(ObjectEditView):
    queryset = ComponentAllocation.objects.all()
    model = ComponentAllocation
    model_form = ComponentAllocationForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'components:componentallocation_list'


class ComponentAllocationDeleteView(ObjectDeleteView):
    queryset = ComponentAllocation.objects.all()
    model = ComponentAllocation
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('components:componentallocation_list')


class ComponentStockAdjustView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        from django.http import HttpResponse
        from django.utils.html import format_html
        from django.urls import reverse
        
        stock = get_object_or_404(ComponentStock, pk=pk)
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
            reverse('components:componentstock_adjust', kwargs={'pk': stock.pk}) + '?action=decrement',
            stock.qty,
            reverse('components:componentstock_adjust', kwargs={'pk': stock.pk}) + '?action=increment'
        ))

