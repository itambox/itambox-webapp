from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django_tables2 import RequestConfig

from ..models import AssetType
from .. import forms, tables, filters

from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView, ObjectCloneView,
)
from assetbox.quick_add import QuickAddMixin


class AssetTypeListView(ObjectListView):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.AssetTypeFilterSet
    filterset_form = forms.AssetTypeFilterForm
    table = tables.AssetTypeTable
    action_buttons = ('add',)


class AssetTypeDetailView(ObjectDetailView):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags', 'assets')
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    layout = (
        ((Panel('info', 'Asset Type Details'), Panel('specs', 'Hardware Specifications')),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assettype = self.get_object()

        assets_table = tables.AssetTable(assettype.assets.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = assettype.assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?asset_type={assettype.slug}"
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class AssetTypeEditView(QuickAddMixin, ObjectEditView):
    queryset = AssetType.objects.all()
    model = AssetType
    model_form = forms.AssetTypeForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_asset_type'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'


class AssetTypeDeleteView(ObjectDeleteView):
    queryset = AssetType.objects.all()
    model = AssetType
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assettype_list')
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def post(self, request, *args, **kwargs):
        assettype = self.get_object()
        asset_count = assettype.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete asset type '{assettype}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(assettype.get_absolute_url())

        return super().post(request, *args, **kwargs)


class AssetTypeCloneView(ObjectCloneView):
    model = AssetType
    model_form = forms.AssetTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assettype_list'


class AssetTypeImportView(ObjectImportView):
    model_form = forms.AssetTypeBulkImportForm
