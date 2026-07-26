from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from inventory.services import (
    checkin_component,
    checkout_inventory_item,
    recipient_assignment_union,
    shared_stock_union,
)
from itambox.panels import Panel
from itambox.quick_add import QuickAddMixin
from itambox.views.generic import (
    ObjectCloneView,
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView

from .. import filters, forms, tables
from ..models import Component, ComponentAllocation, ComponentStock
from .stock_actions import StockAdjustView, StockCreateModalView


class ComponentListView(ObjectListView):
    queryset = Component.objects.with_counts().select_related("manufacturer", "category").prefetch_related("tags")
    filterset = filters.ComponentFilterSet
    filterset_form = forms.ComponentFilterForm
    table = tables.ComponentTable
    action_buttons = ("add",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Components")
        context["breadcrumbs"] = [
            (reverse("dashboard"), _("Dashboard")),
            (None, _("Inventory & Stock")),
            (None, _("Components")),
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from assets.models import Asset
            from organization.models import AssetHolder, Location

            context["asset_holders"] = AssetHolder.objects.all().order_by("last_name", "first_name")
            context["locations"] = Location.objects.all().order_by("name")
            context["assets"] = Asset.objects.all().order_by("asset_tag")
        return context


class ComponentDetailView(ObjectDetailView):
    queryset = Component.objects.select_related("manufacturer", "category").prefetch_related(
        "tags", "stocks", "allocations"
    )
    template_name = "inventory/components/component_detail.html"

    layout = (
        ((Panel("metrics", _("Metrics Overview")),),),
        ((Panel("info", _("Component Details")),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        component = self.get_object()

        stocks_table = tables.ComponentStockTable(component.stocks.all(), request=self.request)
        stocks_table.configure(self.request)
        context["stocks_table"] = stocks_table

        allocations_table = tables.ComponentAllocationTable(
            component.allocations.filter(deleted_at__isnull=True), request=self.request
        )
        allocations_table.configure(self.request)
        context["allocations_table"] = allocations_table

        return context


class ComponentEditView(ObjectEditView):
    queryset = Component.objects.all()
    model = Component
    model_form = forms.ComponentForm
    template_name = "generic/object_edit.html"
    default_return_url = "inventory:component_list"


class ComponentDeleteView(ObjectDeleteView):
    queryset = Component.objects.all()
    model = Component
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("inventory:component_list")


class ComponentCloneView(ObjectCloneView):
    model = Component
    model_form = forms.ComponentForm
    template_name = "generic/object_edit.html"
    default_return_url = "inventory:component_list"


class ComponentStockListView(ObjectListView):
    queryset = ComponentStock.objects.select_related("component", "location")

    def get_queryset(self):
        # ADR-0001 4b: include pools shared TO the active tenant (read-only).
        return shared_stock_union(super().get_queryset(), ComponentStock).select_related("component", "location")

    filterset = filters.ComponentStockFilterSet
    filterset_form = forms.ComponentStockFilterForm
    table = tables.ComponentStockTable
    action_buttons = ("add",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Component Stocks")
        context["breadcrumbs"] = [
            (reverse("dashboard"), _("Dashboard")),
            (reverse("inventory:component_list"), _("Components")),
            (None, _("Stocks")),
        ]
        if not (self.is_htmx_partial() and self.content_partial_name):
            from assets.models import Asset
            from organization.models import AssetHolder, Location

            context["asset_holders"] = AssetHolder.objects.all().order_by("last_name", "first_name")
            context["locations"] = Location.objects.all().order_by("name")
            context["assets"] = Asset.objects.all().order_by("asset_tag")
        return context


class ComponentStockEditView(ObjectEditView):
    queryset = ComponentStock.objects.all()
    model = ComponentStock
    model_form = forms.ComponentStockForm
    template_name = "generic/object_edit.html"
    default_return_url = "inventory:component_list"


class ComponentStockDeleteView(ObjectDeleteView):
    queryset = ComponentStock.objects.all()
    model = ComponentStock
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("inventory:component_list")


class ComponentAllocationListView(ObjectListView):
    queryset = ComponentAllocation.objects.select_related(
        "component", "assigned_holder", "assigned_location", "assigned_asset"
    ).prefetch_related("tags")

    def get_queryset(self):
        # ADR-0001 4b: recipients see allocations targeting their tenant.
        return recipient_assignment_union(
            super().get_queryset(),
            ComponentAllocation,
        ).select_related("component", "assigned_holder", "assigned_location", "assigned_asset")

    filterset = filters.ComponentAllocationFilterSet
    filterset_form = forms.ComponentAllocationFilterForm
    table = tables.ComponentAllocationTable
    action_buttons = ("add",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Component Allocations")
        context["breadcrumbs"] = [
            (reverse("dashboard"), _("Dashboard")),
            (reverse("inventory:component_list"), _("Components")),
            (None, _("Allocations")),
        ]
        return context


class ComponentAllocationEditView(QuickAddMixin, ObjectEditView):
    queryset = ComponentAllocation.objects.all()
    model = ComponentAllocation
    model_form = forms.ComponentAllocationForm
    template_name = "generic/object_edit.html"
    default_return_url = "inventory:component_list"
    quick_add_reload = True

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get("asset")
        if asset_id:
            initial["assigned_asset"] = asset_id
        return initial

    def get_quick_add_redirect_url(self):
        # ComponentAllocation targets assigned_asset (not `asset`); reload back to
        # that asset's detail after a quick-add from its Components tab.
        asset = getattr(self.object, "assigned_asset", None)
        if asset is not None:
            return asset.get_absolute_url()
        return super().get_quick_add_redirect_url()


class ComponentAllocationDeleteView(ObjectDeleteView):
    queryset = ComponentAllocation.objects.all()
    model = ComponentAllocation
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("inventory:component_list")


class ComponentStockAdjustView(StockAdjustView):
    permission_required = "inventory.change_componentstock"
    # Adjustments are owner-only; shared pools remain read/consume-only.
    queryset = ComponentStock.objects.all()


class ComponentStockCreateModalView(StockCreateModalView):
    permission_required = "inventory.add_componentstock"
    queryset = Component.objects.all()
    modal_form = forms.ComponentStockModalForm


class ComponentCheckoutView(GenericTransactionView):
    permission_required = ("inventory.change_component",)
    queryset = Component.objects.all()
    model_form = forms.ComponentCheckoutForm
    service_callable = checkout_inventory_item
    context_object_name = "component"
    template_name = "inventory/includes/component_checkout_modal.html"
    error_partial = "inventory/includes/component_checkout_modal.html#checkout-modal-form"
    success_message = _("Component checked out successfully.")
    form_field_map = {
        "assigned_holder": "holder",
        "assigned_location": "location",
        "assigned_asset": "asset",
        "from_location": "source_location",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs["instance"]
        kwargs["component"] = self.get_object()
        if "initial" not in kwargs:
            kwargs["initial"] = {}
        for key in self.request.GET:
            kwargs["initial"][key] = self.request.GET.get(key)
        return kwargs


class ComponentCheckinView(SimplePostView):
    permission_required = ("inventory.change_component",)
    queryset = ComponentAllocation.objects.all()

    def get_queryset(self):
        # ADR-0001 4b: the recipient tenant may run the return workflow.
        return recipient_assignment_union(super().get_queryset(), ComponentAllocation)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = self.get_object()
        if self.request.user.has_perms(perms, obj=obj):
            return True
        # Recipient side: the same permission, held in the TARGET tenant.
        target = obj.target_tenant
        return target is not None and self.request.user.has_perms(perms, obj=target)

    def perform_action(self, assignment, request):
        component, qty, recipient = checkin_component(assignment.pk, user=request.user)
        return {
            "message": str(
                _("Checked in %(qty)sx '%(component)s' from %(recipient)s.")
                % {"qty": qty, "component": component, "recipient": recipient}
            ),
            "redirect": component.get_absolute_url(),
        }

    def get_success_redirect(self, obj, result):
        return redirect(result.get("redirect") or "/")
