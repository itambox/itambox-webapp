from django.urls import reverse_lazy

from ..models import Depreciation
from .. import forms, tables, filters

from core.panels import Panel
from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)


class DepreciationListView(ObjectListView):
    queryset = Depreciation.objects.all()
    filterset = filters.DepreciationFilterSet
    filterset_form = forms.DepreciationFilterForm
    table = tables.DepreciationTable
    action_buttons = ('add',)


class DepreciationDetailView(ObjectDetailView):
    queryset = Depreciation.objects.all().prefetch_related('asset_types')

    layout = (
        ((Panel('info', 'Depreciation Rule Details'),),),
    )


class DepreciationEditView(ObjectEditView):
    queryset = Depreciation.objects.all()
    model = Depreciation
    model_form = forms.DepreciationForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:depreciation_list'


class DepreciationDeleteView(ObjectDeleteView):
    queryset = Depreciation.objects.all()
    model = Depreciation
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:depreciation_list')
