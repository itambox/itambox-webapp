from django.urls import reverse, reverse_lazy
from django_tables2 import RequestConfig

from ..models import Supplier, Asset
from .. import forms, tables, filters

from core.utils import get_paginate_count
from core.panels import Panel
from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectCloneView,
)


class SupplierListView(ObjectListView):
    queryset = Supplier.objects.all()
    filterset = filters.SupplierFilterSet
    filterset_form = forms.SupplierFilterForm
    table = tables.SupplierTable
    action_buttons = ("add",)


class SupplierDetailView(ObjectDetailView):
    queryset = Supplier.objects.all()

    layout = (
        ((Panel('info', 'Supplier Details'),),),
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

        related_objects_list = []
        asset_count = supplier_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?supplier={supplier.slug}"
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
