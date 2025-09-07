from django.utils import timezone
from django.urls import reverse, reverse_lazy

from ..models import AssetRequest
from .. import forms, tables, filters

from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)


class AssetRequestListView(ObjectListView):
    queryset = AssetRequest.objects.select_related("requester", "asset", "asset_type").all()
    filterset = filters.AssetRequestFilterSet
    filterset_form = forms.AssetRequestFilterForm
    table = tables.AssetRequestTable
    action_buttons = ("add",)


class AssetRequestDetailView(ObjectDetailView):
    queryset = AssetRequest.objects.select_related("requester", "asset", "asset_type", "responded_by").all()

    layout = (
        (
            (Panel('info', 'Asset Request Details'),),
            (Panel('response', 'Decision & Response Details'),),
        ),
    )


class AssetRequestCreateView(ObjectEditView):
    model = AssetRequest
    model_form = forms.AssetRequestForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:assetrequest_list"

    def form_valid(self, form):
        form.instance.requester = self.request.user
        return super().form_valid(form)


class AssetRequestEditView(ObjectEditView):
    queryset = AssetRequest.objects.all()
    model = AssetRequest
    model_form = forms.AssetRequestResponseForm
    template_name = "generic/object_edit.html"

    def form_valid(self, form):
        if form.instance.status in ("approved", "denied", "fulfilled", "cancelled"):
            form.instance.response_date = timezone.now()
            form.instance.responded_by = self.request.user
        response = super().form_valid(form)
        try:
            from core.events import dispatch_event
            from core.models import Notification
            dispatch_event(AssetRequest, self.object, action='update')
            Notification.objects.create(
                user=self.object.requester,
                subject=f"Asset Request {self.object.get_status_display()}",
                message=f"Your request for {self.object} has been {self.object.get_status_display().lower()}.",
                level=Notification.LEVEL_INFO,
                target_url=self.object.get_absolute_url(),
            )
        except Exception:
            pass
        return response

    def get_success_url(self):
        if self.object:
            return self.object.get_absolute_url()
        return reverse("assets:assetrequest_list")


class AssetRequestQueueView(ObjectListView):
    queryset = AssetRequest.objects.filter(status=AssetRequest.STATUS_PENDING).select_related("requester", "asset", "asset_type")
    filterset = filters.AssetRequestFilterSet
    filterset_form = forms.AssetRequestFilterForm
    table = tables.AssetRequestTable
    action_buttons = ()
    template_name = 'generic/object_list.html'


class AssetRequestDeleteView(ObjectDeleteView):
    queryset = AssetRequest.objects.all()
    model = AssetRequest
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:assetrequest_list")
