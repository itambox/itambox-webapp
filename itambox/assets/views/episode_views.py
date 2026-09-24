from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.filters import RepairEpisodeFilterSet
from assets.forms import RepairEpisodeFilterForm, RepairEpisodeForm
from assets.models import RepairEpisode
from assets.tables import RepairEpisodeTable
from itambox.panels import Panel
from itambox.quick_add import QuickAddMixin
from itambox.views.generic import (
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)


class RepairEpisodeListView(ObjectListView):
    queryset = RepairEpisode.objects.select_related("asset", "substitute_asset")
    filterset = RepairEpisodeFilterSet
    filterset_form = RepairEpisodeFilterForm
    table = RepairEpisodeTable
    action_buttons = ("add",)


class RepairEpisodeDetailView(ObjectDetailView):
    queryset = RepairEpisode.objects.select_related("asset", "substitute_asset")
    template_name = "generic/object_detail.html"

    layout = (((Panel("info", _("Repair Episode")),),),)

    def get_object_display(self, obj):
        return str(obj)


class RepairEpisodeEditView(QuickAddMixin, ObjectEditView):
    queryset = RepairEpisode.objects.all()
    model = RepairEpisode
    model_form = RepairEpisodeForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:repairepisode_list"
    quick_add_reload = True

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get("asset")
        if asset_id:
            initial["asset"] = asset_id
        return initial


class RepairEpisodeDeleteView(ObjectDeleteView):
    queryset = RepairEpisode.objects.all()
    model = RepairEpisode
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:repairepisode_list")

    def get_object_display(self):
        return str(self.object)
