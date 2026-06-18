from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig

from ..models import AssetType
from .. import forms, tables, filters

from itambox.utils import get_paginate_count
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView, ObjectCloneView,
)
from itambox.quick_add import QuickAddMixin


class AssetTypeListView(ObjectListView):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.AssetTypeFilterSet
    filterset_form = forms.AssetTypeFilterForm
    table = tables.AssetTypeTable
    action_buttons = ('add',)


class AssetTypeDetailView(ObjectDetailView):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags', 'assets')

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
                'url': f"{reverse('assets:asset_list')}?asset_type={assettype.pk}"
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list

        # Requests
        from ..models import AssetRequest
        req_qs = AssetRequest.objects.filter(asset_type=assettype).select_related('requester', 'asset', 'asset_type')
        requests_table = tables.AssetRequestTable(req_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(requests_table)
        context['requests_table'] = requests_table

        # Kits
        from inventory.models import Kit
        from inventory.tables import KitTable
        kits_qs = Kit.objects.filter(items__asset_type=assettype).distinct().select_related('tenant')
        kits_table = KitTable(kits_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(kits_table)
        context['kits_table'] = kits_table

        return context


class AssetTypeEditView(QuickAddMixin, ObjectEditView):
    queryset = AssetType.objects.all()
    model = AssetType
    model_form = forms.AssetTypeForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_asset_type'

    def post(self, request, *args, **kwargs):
        if request.headers.get('HX-Request') and '_reload' in request.POST:
            self.object = self.get_object() if self.kwargs.get('pk') else None
            form = self.get_form()
            from django.shortcuts import render
            return render(request, 'htmx/crispy_form.html', {'form': form})
        return super().post(request, *args, **kwargs)


class AssetTypeDeleteView(ObjectDeleteView):
    queryset = AssetType.objects.all()
    model = AssetType
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assettype_list')

    def post(self, request, *args, **kwargs):
        assettype = self.get_object()
        asset_count = assettype.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                _("Cannot delete asset type '%(type)s': It is associated with %(count)s asset%(suffix)s.") % {
                    "type": assettype,
                    "count": asset_count,
                    "suffix": 's' if asset_count != 1 else '',
                }
            )
            return redirect(assettype.get_absolute_url())

        return super().post(request, *args, **kwargs)


class AssetTypeCloneView(ObjectCloneView):
    model = AssetType
    model_form = forms.AssetTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assettype_list'
