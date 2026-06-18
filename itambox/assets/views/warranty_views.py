from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)

from assets.models import Warranty
from assets.tables import WarrantyTable
from assets.forms import WarrantyForm, WarrantyFilterForm
from assets.filters import WarrantyFilterSet


class WarrantyListView(ObjectListView):
    queryset = Warranty.objects.select_related('asset')
    filterset = WarrantyFilterSet
    filterset_form = WarrantyFilterForm
    table = WarrantyTable
    action_buttons = ('add',)


class WarrantyDetailView(ObjectDetailView):
    queryset = Warranty.objects.select_related('asset')
    template_name = 'generic/object_detail.html'

    layout = (
        ((Panel('info', _('Warranty Details')),),),
    )


class WarrantyEditView(ObjectEditView):
    queryset = Warranty.objects.all()
    model = Warranty
    model_form = WarrantyForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:warranty_list'

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        return initial


class WarrantyDeleteView(ObjectDeleteView):
    queryset = Warranty.objects.all()
    model = Warranty
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:warranty_list')
