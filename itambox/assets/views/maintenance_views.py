from django.urls import reverse_lazy

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectCloneView
)

from assets.models import AssetMaintenance
from assets.tables import AssetMaintenanceTable
from compliance.filters import AssetMaintenanceFilterSet
from compliance.forms import AssetMaintenanceForm, AssetMaintenanceFilterForm


class AssetMaintenanceListView(ObjectListView):
    queryset = AssetMaintenance.objects.select_related('asset', 'supplier')
    filterset = AssetMaintenanceFilterSet
    filterset_form = AssetMaintenanceFilterForm
    table = AssetMaintenanceTable
    action_buttons = ('add',)


class AssetMaintenanceDetailView(ObjectDetailView):
    queryset = AssetMaintenance.objects.select_related('asset')
    template_name = 'compliance/assetmaintenances/assetmaintenance_detail.html'

    layout = (
        ((Panel('metrics', 'Maintenance Overview'),),),
        ((Panel('info', 'Maintenance Details'),),),
    )


class AssetMaintenanceEditView(ObjectEditView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    model_form = AssetMaintenanceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assetmaintenance_list'

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        return initial


class AssetMaintenanceCloneView(AssetMaintenanceEditView, ObjectCloneView):
    model = AssetMaintenance


class AssetMaintenanceDeleteView(ObjectDeleteView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetmaintenance_list')
