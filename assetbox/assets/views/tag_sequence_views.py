from django.urls import reverse_lazy

from ..models import AssetTagSequence
from .. import forms, tables, filters

from core.panels import Panel
from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)


class AssetTagSequenceListView(ObjectListView):
    queryset = AssetTagSequence.objects.all()
    filterset = filters.AssetTagSequenceFilterSet
    filterset_form = forms.AssetTagSequenceFilterForm
    table = tables.AssetTagSequenceTable
    action_buttons = ('add',)


class AssetTagSequenceDetailView(ObjectDetailView):
    queryset = AssetTagSequence.objects.all()

    layout = (
        ((Panel('info', 'Asset Tag Sequence Details'),),),
    )


class AssetTagSequenceEditView(ObjectEditView):
    queryset = AssetTagSequence.objects.all()
    model = AssetTagSequence
    model_form = forms.AssetTagSequenceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assettagsequence_list'


class AssetTagSequenceDeleteView(ObjectDeleteView):
    queryset = AssetTagSequence.objects.all()
    model = AssetTagSequence
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assettagsequence_list')
