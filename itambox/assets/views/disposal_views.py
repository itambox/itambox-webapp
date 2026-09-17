from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.filters import AssetDisposalFilterSet
from assets.forms import AssetDisposalFilterForm, AssetDisposalForm
from assets.forms.disposal_form import AssetDisposalCancelForm
from assets.models import Asset, AssetDisposal
from assets.services import (
    cancel_asset_disposal,
    disposal_service_payload,
    dispose_asset,
    update_asset_disposal,
)
from assets.tables import AssetDisposalTable
from itambox.panels import Panel
from itambox.quick_add import QuickAddMixin
from itambox.views.generic import (
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)
from itambox.views.generic.service_views import GenericTransactionView


class AssetDisposalListView(ObjectListView):
    """Disposal history: active records and cancelled (preserved) records."""

    # #496 repair14: the history includes tombstones (soft-deleted, uncancelled records are
    # still active evidence), resolved through the tenant-safe manager that includes them.
    queryset = AssetDisposal.all_objects.select_related("asset", "asset__asset_type__manufacturer", "cancelled_by")
    filterset = AssetDisposalFilterSet
    filterset_form = AssetDisposalFilterForm
    table = AssetDisposalTable
    action_buttons = ("add",)


class AssetDisposalDetailView(ObjectDetailView):
    # The visible "View" links of a tombstone must resolve (#496 repair14).
    queryset = AssetDisposal.all_objects.select_related(
        "asset", "asset__asset_type__manufacturer", "asset__tenant", "cancelled_by"
    )
    template_name = "assets/assetdisposal_detail.html"

    layout = (
        ((Panel("overview", _("Disposal Overview")),),),
        ((Panel("sanitization", _("Data Sanitization")),),),
        ((Panel("financial", _("Financial / Proceeds")),),),
        ((Panel("cancellation", _("Cancellation")),),),
    )


class AssetDisposalEditView(QuickAddMixin, ObjectEditView):
    """Record (or amend) a disposal record through the disposal services.

    Creating a record from here is a LIFECYCLE operation (#496): it runs through
    ``dispose_asset``, so the record, the asset's disposal stamps/status and the
    auto-check-in are one atomic operation exactly like the dedicated dispose
    action. Amending an existing record goes through ``update_asset_disposal``,
    which keeps the asset identity immutable and re-synchronizes the asset
    snapshot so the two cannot drift apart.
    """

    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    model_form = AssetDisposalForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:assetdisposal_list"
    quick_add_reload = True

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get("asset")
        if asset_id:
            initial["asset"] = asset_id
        return initial

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            if self.object is None:
                # B1: re-fetch through the tenant-scoped manager. The form's
                # asset queryset is import-frozen and unscoped, so a crafted POST
                # could otherwise dispose another tenant's asset by pk.
                asset = get_object_or_404(Asset.objects, pk=data["asset"].pk)
                self.object = dispose_asset(asset=asset, user=self.request.user, **disposal_service_payload(data))
            else:
                self.object = update_asset_disposal(
                    self.object,
                    user=self.request.user,
                    data=data,
                    request=self.request,
                )
        except DjangoValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        if self.is_quick_add():
            return self.get_quick_add_success_response()
        messages.success(
            self.request,
            _("Disposal for '%(asset)s' saved.") % {"asset": self.object.asset},
        )
        return HttpResponseRedirect(self.object.get_absolute_url())


class AssetDisposalCancelView(GenericTransactionView):
    """Cancel an erroneous disposal without erasing its evidence (#496).

    Reuses the existing ``assets.dispose_asset`` authority on the record's asset;
    the actor is taken from the request (no staff/superuser shortcut) and the
    reason is mandatory.
    """

    permission_required = ("assets.dispose_asset",)
    # #496 repair12: the blocking record may be a tombstone (soft-deleted, not cancelled),
    # so the cancel view must resolve it through the tenant-safe manager that includes
    # them. Tenant narrowing still happens in SecuredObjectActionMixin.get_queryset
    # (a foreign record stays a 404).
    queryset = AssetDisposal.all_objects.select_related("asset", "asset__tenant")
    model_form = AssetDisposalCancelForm
    service_callable = cancel_asset_disposal
    context_object_name = "disposal"
    template_name = "assets/assetdisposal_cancel.html"
    success_message = _("Disposal cancelled. The record is preserved and the asset is pending.")
    hx_trigger = "tableRefreshRequired"
    hx_redirect_on_success = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs["instance"]
        return kwargs


class AssetDisposalDeleteView(ObjectDeleteView):
    """Refused on purpose: a disposal record is evidence and is never deleted (#496).

    The route is kept so the generic detail page still resolves its delete action;
    any attempt is answered with a visible error pointing at cancellation instead
    of presenting a confirmation page that cannot succeed.
    """

    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:assetdisposal_list")

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.has_permission():
            raise PermissionDenied
        messages.error(
            request,
            _("Disposal records are lifecycle evidence and cannot be deleted. Cancel the disposal instead."),
        )
        return redirect(self.object.get_absolute_url())


class AssetDisposeActionView(ObjectEditView):
    """
    Dedicated 'dispose this asset' action view.

    Navigates to via /assets/<pk>/dispose/ — pre-fills the asset field and
    calls the dispose_asset() service on successful form submission so that
    the asset status / disposed_at fields are updated atomically alongside the
    disposal record. The URL's asset is authoritative: the form field is
    disabled, and the service re-fetches the asset through the tenant-scoped
    manager.
    """

    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    model_form = AssetDisposalForm
    template_name = "assets/assetdispose_action.html"
    default_return_url = "assets:asset_list"

    def get_object(self, queryset=None):
        # The asset pk comes from the URL; the disposal may or may not exist yet.
        asset = get_object_or_404(Asset, pk=self.kwargs["pk"])
        return asset.active_disposal

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # The asset is fixed by the URL — never let a POST re-point the disposal.
        form.fields["asset"].disabled = True
        return form

    def get_initial(self):
        initial = super().get_initial() or {}
        initial["asset"] = self.kwargs.get("pk")
        return initial

    def form_valid(self, form):
        data = form.cleaned_data
        # B1: re-fetch the asset through the tenant-scoped manager before
        # disposing. The URL pk is authoritative (the form field is disabled), so
        # a crafted POST cannot dispose a different asset.
        asset = get_object_or_404(Asset.objects, pk=self.kwargs["pk"])
        try:
            disposal = dispose_asset(asset=asset, user=self.request.user, **disposal_service_payload(data))
        except DjangoValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request, _("Asset '%(asset)s' has been marked as disposed and archived.") % {"asset": asset}
        )
        return HttpResponseRedirect(disposal.get_absolute_url())
