from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.models import AssetMaintenance
from assets.tables import AssetMaintenanceTable
from compliance.filters import AssetMaintenanceFilterSet
from compliance.forms import AssetMaintenanceFilterForm, AssetMaintenanceForm
from itambox.panels import Panel
from itambox.quick_add import QuickAddMixin
from itambox.views.generic import ObjectCloneView, ObjectDeleteView, ObjectDetailView, ObjectEditView, ObjectListView


class AssetMaintenanceListView(ObjectListView):
    queryset = AssetMaintenance.objects.select_related("asset", "supplier")
    filterset = AssetMaintenanceFilterSet
    filterset_form = AssetMaintenanceFilterForm
    table = AssetMaintenanceTable
    action_buttons = ("add",)


class AssetMaintenanceDetailView(ObjectDetailView):
    queryset = AssetMaintenance.objects.select_related("asset")
    template_name = "compliance/assetmaintenances/assetmaintenance_detail.html"

    layout = (
        ((Panel("metrics", _("Maintenance Overview")),),),
        ((Panel("info", _("Maintenance Details")),),),
    )


class AssetMaintenanceEditView(QuickAddMixin, ObjectEditView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    model_form = AssetMaintenanceForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:assetmaintenance_list"
    # When opened as a quick-add modal from an asset's Maintenances tab, save and
    # reload back to the asset detail (mirrors WarrantyEditView).
    quick_add_reload = True

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get("asset")
        if asset_id:
            initial["asset"] = asset_id
        # "Log repair" on the asset timeline opens this quick-add with the repair
        # type preselected (#644).
        maintenance_type = self.request.GET.get("maintenance_type")
        if maintenance_type:
            initial["maintenance_type"] = maintenance_type
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        """Re-render the repair form when a lifecycle service refuses (#644).

        Issuing a loaner and closing a repair run through the services, which fail
        closed on an unavailable or foreign-tenant unit or with no holder to lend
        to. That refusal is a visible field error, never a 500 and never a
        half-written record: the form's own transaction rolls the maintenance back.
        """
        try:
            return super().form_valid(form)
        except DjangoValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)


class AssetMaintenanceCloneView(AssetMaintenanceEditView, ObjectCloneView):
    model = AssetMaintenance


class AssetMaintenanceDeleteView(ObjectDeleteView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:assetmaintenance_list")
