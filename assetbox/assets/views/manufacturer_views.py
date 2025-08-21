from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django.db.models import Count
from django_tables2 import RequestConfig

from ..models import Manufacturer, Asset
from .. import forms, tables, filters

from core.utils import get_paginate_count
from core.panels import Panel
from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView,
)


class ManufacturerListView(ObjectListView):
    queryset = Manufacturer.objects.annotate(
        asset_count=Count('asset_types__assets'),
        asset_type_count=Count('asset_types'),
    )
    filterset = filters.ManufacturerFilterSet
    filterset_form = forms.ManufacturerFilterForm
    table = tables.ManufacturerTable
    action_buttons = ('add',)


class ManufacturerDetailView(ObjectDetailView):
    queryset = Manufacturer.objects.prefetch_related(
        'asset_types', 'asset_types__assets'
    )

    layout = (
        ((Panel('info', 'Manufacturer Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manufacturer = self.get_object()

        asset_types_table = tables.AssetTypeTable(manufacturer.asset_types.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)

        manufacturer_assets = Asset.objects.filter(asset_type__manufacturer=manufacturer).select_related(
            'asset_role', 'asset_type', 'location'
        )
        assets_table = tables.AssetTable(manufacturer_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = manufacturer_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?manufacturer={manufacturer.slug}"
            })
        assettype_count = manufacturer.asset_types.count()
        if assettype_count:
            related_objects_list.append({
                'label': 'Asset Types',
                'count': assettype_count,
                'url': f"{reverse('assets:assettype_list')}?manufacturer={manufacturer.slug}"
            })

        context['asset_types_table'] = asset_types_table
        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class ManufacturerEditView(ObjectEditView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    model_form = forms.ManufacturerForm
    template_name = 'generic/object_edit.html'


class ManufacturerDeleteView(ObjectDeleteView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:manufacturer_list')

    def post(self, request, *args, **kwargs):
        manufacturer = self.get_object()
        asset_type_count = manufacturer.asset_types.count()

        if asset_type_count > 0:
            messages.error(
                request,
                f"Cannot delete manufacturer '{manufacturer.name}': It is associated with {asset_type_count} asset type{'s' if asset_type_count != 1 else ''}."
            )
            return redirect(manufacturer.get_absolute_url())

        return super().post(request, *args, **kwargs)


class ManufacturerImportView(ObjectImportView):
    model_form = forms.ManufacturerBulkImportForm
