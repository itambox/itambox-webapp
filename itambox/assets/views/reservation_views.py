from django.urls import reverse_lazy

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)

from assets.models import AssetReservation
from assets.tables import AssetReservationTable
from assets.forms import AssetReservationForm, AssetReservationFilterForm
from assets.filters import AssetReservationFilterSet


class AssetReservationListView(ObjectListView):
    queryset = AssetReservation.objects.select_related('asset', 'reserved_for', 'created_by')
    filterset = AssetReservationFilterSet
    filterset_form = AssetReservationFilterForm
    table = AssetReservationTable
    action_buttons = ('add',)


class AssetReservationDetailView(ObjectDetailView):
    queryset = AssetReservation.objects.select_related('asset', 'reserved_for', 'created_by')
    template_name = 'generic/object_detail.html'

    layout = (
        ((Panel('info', 'Reservation Details'),),),
    )


class AssetReservationEditView(ObjectEditView):
    queryset = AssetReservation.objects.all()
    model = AssetReservation
    model_form = AssetReservationForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assetreservation_list'

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        if self.request.user and self.request.user.is_authenticated:
            initial.setdefault('created_by', self.request.user.pk)
        return initial


class AssetReservationDeleteView(ObjectDeleteView):
    queryset = AssetReservation.objects.all()
    model = AssetReservation
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetreservation_list')
