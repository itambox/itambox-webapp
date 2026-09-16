from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.services import checkout_kit
from core.context import override_current_tenant_scope
from core.tables.constants import TABLE_EMPTY_VALUE
from core.tenant_access import active_membership
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectCloneView,
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)
from itambox.views.generic.htmx_responses import is_htmx_request
from itambox.views.generic.service_views import GenericTransactionView

from .. import filters, forms, tables
from ..forms.kit_forms import kit_target_tenant, kit_target_tenant_queryset
from ..models import Kit, KitItem
from ..services import kit_availability, kit_availability_tenant


class KitListView(ObjectListView):
    queryset = Kit.objects.select_related("tenant").annotate(item_count=Count("items"))
    filterset = filters.KitFilterSet
    filterset_form = forms.KitFilterForm
    table = tables.KitTable
    action_buttons = ("add",)


class KitDetailView(ObjectDetailView):
    queryset = Kit.objects.all().prefetch_related(
        "items__asset_type", "items__accessory", "items__license__software", "items__consumable"
    )
    template_name = "assets/kits/kit_detail.html"

    layout = (((Panel("info", _("Kit Details")),),),)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Availability is measured against the kit's OWNING tenant only: the
        # aggregate scopes (all-accessible, tenant group) must never add other
        # tenants' devices or stock pools to a kit they do not own, and a
        # tenantless template has no owner to measure -- unknown, never a total.
        items = list(self.object.items.all())
        rows, state = kit_availability(
            items,
            kit_availability_tenant(self.object, getattr(self.request, "active_tenant", None)),
        )
        context["items_with_availability"] = rows
        # Tri-state, never a certification of an unverified resource: the page
        # must not render a verified-available affordance for a kit whose
        # required resource could not be attributed to its owning tenant.
        context["availability_state"] = state
        context["availability_needs_target"] = any(row["needs_target"] for row in rows)
        context["all_available"] = state == "available"
        context["table_empty_value"] = TABLE_EMPTY_VALUE
        return context


class KitEditView(ObjectEditView):
    queryset = Kit.objects.all()
    model = Kit
    model_form = forms.KitForm
    template_name = "generic/object_edit.html"
    default_return_url = "inventory:kit_list"


class KitCloneView(KitEditView, ObjectCloneView):
    model = Kit


class KitDeleteView(ObjectDeleteView):
    queryset = Kit.objects.all()
    model = Kit
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("inventory:kit_list")


class KitItemEditView(ObjectEditView):
    queryset = KitItem.objects.all()
    model = KitItem
    model_form = forms.KitItemForm
    template_name = "generic/object_edit.html"

    def get_initial(self):
        initial = super().get_initial()
        kit_id = self.request.GET.get("kit")
        if kit_id:
            initial["kit"] = kit_id
        return initial

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse("inventory:kit_list")


class KitItemDeleteView(ObjectDeleteView):
    queryset = KitItem.objects.all()
    model = KitItem
    template_name = "generic/object_confirm_delete.html"

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse("inventory:kit_list")


class KitCheckoutView(GenericTransactionView):
    permission_required = ("inventory.change_kit",)
    queryset = Kit.objects.all()
    model_form = forms.KitCheckoutForm
    service_callable = checkout_kit
    context_object_name = "kit"
    template_name = "inventory/includes/kit_checkout_modal.html"
    error_partial = "inventory/includes/kit_checkout_modal.html#checkout-modal-form"
    success_message = _("Kit checked out successfully.")
    hx_trigger = "kitListUpdated"
    form_field_map = {
        "assigned_holder": "holder",
        "assigned_location": "location",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs["instance"]
        kwargs["kit"] = self.get_object()
        kwargs["request"] = self.request
        tenant_param = self.request.GET.get("tenant")
        if tenant_param:
            initial = dict(kwargs.get("initial") or {})
            initial.setdefault("tenant", tenant_param)
            kwargs["initial"] = initial
        return kwargs

    def has_permission(self):
        """Retained object guard, plus a per-tenant gate for global kits.

        A global kit has no tenant to anchor the object check on; in that case
        the permission must be proven for the target tenant (the submitted
        candidate for POST, any offered candidate for the open modal).
        """
        if self.get_object().tenant_id is None:
            return self._global_kit_permission()
        return super().has_permission()

    def _global_kit_permission(self):
        permission = self.permission_required[0]
        raw_value = self.request.POST.get("tenant") if self.request.method == "POST" else self.request.GET.get("tenant")
        target = kit_target_tenant(self.request, self.get_object(), raw_value)
        if target is not None:
            return self.request.user.has_perm(permission, obj=target)
        return any(
            self.request.user.has_perm(permission, obj=tenant)
            for tenant in kit_target_tenant_queryset(self.request, self.get_object())
        )

    def post(self, request, *args, **kwargs):
        """Re-render the modal form when the target-tenant choice changes."""
        if is_htmx_request(request) and "_reload" in request.POST:
            self.object = self.get_object()
            form = self._refreshed_form()
            return render(request, self.error_partial, self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def _refreshed_form(self):
        """Refresh the dependent choices without validating untouched fields.

        Picking a target tenant is a presentation step, not a submission: the
        refreshed form keeps the values the new target still allows and shows
        its scoped choices. Reporting "this field is required" or "you must
        select a target" here blamed the operator for fields they had simply
        not reached yet; validation stays with the final submit.
        """
        kwargs = self.get_form_kwargs()
        kwargs.pop("data", None)
        kwargs.pop("files", None)
        kwargs["initial"] = {**self.request.POST.dict(), **(kwargs.get("initial") or {})}
        return self.get_form_class()(**kwargs)

    def form_valid(self, form):
        """Bind the service call to the re-read target tenant and its scope.

        The tenant re-read here is the authority for the whole transaction and
        every scheduled on_commit callback; the previous runtime scope state is
        restored as soon as the operation completed.
        """
        target_tenant = form.cleaned_data.get("target_tenant")
        if target_tenant is None or not self.request.user.has_perm(self.permission_required[0], obj=target_tenant):
            message = str(_("You do not have permission to perform this action."))
            if is_htmx_request(self.request):
                response = self._htmx_error_response(message)
                response.status_code = 403
                return response
            raise PermissionDenied(message)
        with override_current_tenant_scope(target_tenant, active_membership(self.request.user, target_tenant.pk)):
            return super().form_valid(form)

    def get_service_kwargs(self, form):
        service_kwargs = super().get_service_kwargs(form)
        # Per-row device fields are input widgets: the service consumes only
        # the validated ``selected_assets`` mapping. The target-tenant pick is
        # consumed by the view's scope binding — never forwarded as a service
        # keyword.
        for item in getattr(form, "hardware_items", ()):
            service_kwargs.pop(f"asset_{item.pk}", None)
        service_kwargs.pop("tenant", None)
        service_kwargs.pop("target_tenant", None)
        return service_kwargs
