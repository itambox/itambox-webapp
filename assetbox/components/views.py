from django.urls import reverse_lazy
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
    queryset = Component.objects.select_related('manufacturer', 'category').prefetch_related('tags', 'stocks')
    filterset = ComponentFilterSet
    filterset_form = ComponentFilterForm
    table = ComponentTable
    action_buttons = ('add',)


class ComponentDetailView(ObjectDetailView):
    queryset = Component.objects.select_related('manufacturer', 'category').prefetch_related('tags', 'stocks', 'allocations')

    layout = (
        ((Panel('info', 'Component Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        component = self.get_object()

        stocks_table = ComponentStockTable(component.stocks.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(stocks_table)
        context['stocks_table'] = stocks_table

        allocations_table = ComponentAllocationTable(
            component.allocations.filter(deleted_at__isnull=True).select_related('asset'),
            request=self.request
        )
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(allocations_table)
        context['allocations_table'] = allocations_table

        return context


class ComponentEditView(ObjectEditView):
    queryset = Component.objects.all()
    model = Component
    model_form = ComponentForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:component_list'


class ComponentDeleteView(ObjectDeleteView):
    queryset = Component.objects.all()
    model = Component
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:component_list')


class ComponentCloneView(ObjectCloneView):
    model = Component
    model_form = ComponentForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:component_list'


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
    default_return_url = 'assets:componentstock_list'


class ComponentStockDeleteView(ObjectDeleteView):
    queryset = ComponentStock.objects.all()
    model = ComponentStock
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componentstock_list')


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
    default_return_url = 'assets:componentallocation_list'


class ComponentAllocationDeleteView(ObjectDeleteView):
    queryset = ComponentAllocation.objects.all()
    model = ComponentAllocation
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componentallocation_list')
