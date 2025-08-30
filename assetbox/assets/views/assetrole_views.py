from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django_tables2 import RequestConfig
from django.db.models import Count

from ..models import AssetRole
from .. import forms, tables, filters

from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)
from assetbox.quick_add import QuickAddMixin


class AssetRoleListView(ObjectListView):
    queryset = AssetRole.objects.prefetch_related('tags').annotate(asset_count=Count('asset'))
    filterset = filters.AssetRoleFilterSet
    filterset_form = forms.AssetRoleFilterForm
    table = tables.AssetRoleTable
    action_buttons = ('add',)


class AssetRoleDetailView(ObjectDetailView):
    queryset = AssetRole.objects.prefetch_related('tags', 'asset_set')

    layout = (
        ((Panel('info', 'Asset Role Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetrole = self.get_object()

        assets_table = tables.AssetTable(assetrole.asset_set.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = assetrole.asset_set.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?asset_role={assetrole.slug}"
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class AssetRoleEditView(QuickAddMixin, ObjectEditView):
    queryset = AssetRole.objects.all()
    model = AssetRole
    model_form = forms.AssetRoleForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_asset_role'


class AssetRoleDeleteView(ObjectDeleteView):
    queryset = AssetRole.objects.all()
    model = AssetRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetrole_list')

    def post(self, request, *args, **kwargs):
        assetrole = self.get_object()
        asset_count = assetrole.asset_set.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete asset role '{assetrole.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(assetrole.get_absolute_url())

        return super().post(request, *args, **kwargs)
