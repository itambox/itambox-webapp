from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig

from ..models import StatusLabel
from .. import forms, tables, filters

from itambox.utils import get_paginate_count
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectCloneView,
)


class StatusLabelListView(ObjectListView):
    queryset = StatusLabel.objects.annotate(asset_count=Count('assets'))
    filterset = filters.StatusLabelFilterSet
    filterset_form = forms.StatusLabelFilterForm
    table = tables.StatusLabelTable
    action_buttons = ('add',)


class StatusLabelDetailView(ObjectDetailView):
    queryset = StatusLabel.objects.prefetch_related('assets')

    layout = (
        ((Panel('info', 'Status Label Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        statuslabel = self.get_object()

        assets_qs = statuslabel.assets.select_related('asset_role', 'asset_type', 'location')
        assets_table = tables.AssetTable(assets_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = assets_qs.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?status={statuslabel.slug}"
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class StatusLabelEditView(ObjectEditView):
    queryset = StatusLabel.objects.all()
    model = StatusLabel
    model_form = forms.StatusLabelForm
    template_name = 'generic/object_edit.html'


class StatusLabelCloneView(ObjectCloneView):
    model = StatusLabel
    model_form = forms.StatusLabelForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:statuslabel_list'


class StatusLabelDeleteView(ObjectDeleteView):
    queryset = StatusLabel.objects.all()
    model = StatusLabel
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:statuslabel_list')

    def post(self, request, *args, **kwargs):
        statuslabel = self.get_object()
        asset_count = statuslabel.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                _("Cannot delete status label '%(name)s': It is associated with %(count)s asset%(suffix)s.") % {
                    "name": statuslabel.name,
                    "count": asset_count,
                    "suffix": 's' if asset_count != 1 else '',
                }
            )
            return redirect(statuslabel.get_absolute_url())

        return super().post(request, *args, **kwargs)
