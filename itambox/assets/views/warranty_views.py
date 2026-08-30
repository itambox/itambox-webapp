from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.filters import WarrantyFilterSet
from assets.forms import WarrantyFilterForm, WarrantyForm
from assets.models import Warranty
from assets.tables import WarrantyTable
from itambox.panels import Panel
from itambox.quick_add import QuickAddMixin
from itambox.views.generic import (
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)


class WarrantyListView(ObjectListView):
    queryset = Warranty.objects.select_related("asset")
    filterset = WarrantyFilterSet
    filterset_form = WarrantyFilterForm
    table = WarrantyTable
    action_buttons = ("add",)


class WarrantyDetailView(ObjectDetailView):
    queryset = Warranty.objects.select_related("asset")
    template_name = "generic/object_detail.html"

    layout = (((Panel("info", _("Warranty Details")),),),)

    def get_object_display(self, obj):
        return _("%(type)s warranty on %(asset)s (%(start)s to %(end)s)") % {
            "type": obj.get_warranty_type_display(),
            "asset": obj.asset,
            "start": obj.start_date,
            "end": obj.end_date,
        }


class WarrantyEditView(QuickAddMixin, ObjectEditView):
    queryset = Warranty.objects.all()
    model = Warranty
    model_form = WarrantyForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:warranty_list"
    quick_add_reload = True

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get("asset")
        if asset_id:
            initial["asset"] = asset_id
        return initial


class WarrantyDeleteView(ObjectDeleteView):
    queryset = Warranty.objects.all()
    model = Warranty
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:warranty_list")

    def get_object_display(self):
        return _("%(type)s warranty on %(asset)s (%(start)s to %(end)s)") % {
            "type": self.object.get_warranty_type_display(),
            "asset": self.object.asset,
            "start": self.object.start_date,
            "end": self.object.end_date,
        }
