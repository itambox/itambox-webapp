from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from assets.choices import StatusTypeChoices
from assets.models import Asset
from assets.services import checkout_kit
from core.context import override_current_tenant_scope
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
from ..models import Accessory, Consumable, Kit, KitItem


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
        # Check availability of each kit item
        items_with_availability = []
        all_available = True

        items = list(self.object.items.all())
        asset_type_ids = [i.asset_type_id for i in items if i.asset_type_id]
        accessory_ids = [i.accessory_id for i in items if i.accessory_id]
        license_ids = [i.license_id for i in items if i.license_id]
        consumable_ids = [i.consumable_id for i in items if i.consumable_id]

        # 1. Batch Asset Availability Count. The checkout contract is the
        # deployable status TYPE (the service and the per-row device picker use
        # it too), not one hard-coded "available" slug: custom deployable
        # labels must not hide a valid checkout affordance.
        asset_counts = {}
        if asset_type_ids:
            from django.db.models import Count

            counts = (
                Asset.objects.filter(asset_type_id__in=asset_type_ids, status__type=StatusTypeChoices.DEPLOYABLE)
                .values("asset_type_id")
                .annotate(count=Count("id"))
            )
            asset_counts = {c["asset_type_id"]: c["count"] for c in counts}

        # 2. Batch Accessory Available Qty
        accessory_avail = {}
        if accessory_ids:
            from django.db.models import IntegerField, OuterRef, Subquery, Sum

            from ..models import AccessoryAssignment, AccessoryStock

            # Independent Subqueries per reverse relation: summing stocks and
            # assignments in one .annotate() would cross-join them and inflate
            # both totals (|stocks| x |assignments| fan-out).
            stock_sub = (
                AccessoryStock.objects.filter(accessory=OuterRef("pk"))
                .order_by()
                .values("accessory")
                .annotate(total=Sum("qty"))
                .values("total")
            )
            undeducted_sub = (
                AccessoryAssignment.objects.filter(accessory=OuterRef("pk"), from_location__isnull=True)
                .order_by()
                .values("accessory")
                .annotate(total=Sum("qty"))
                .values("total")
            )
            stocks = (
                Accessory.objects.filter(id__in=accessory_ids)
                .annotate(
                    total_qty=Coalesce(Subquery(stock_sub, output_field=IntegerField()), 0),
                    undeducted_qty=Coalesce(Subquery(undeducted_sub, output_field=IntegerField()), 0),
                )
                .values("id", "total_qty", "undeducted_qty")
            )
            for s in stocks:
                accessory_avail[s["id"]] = max(0, s["total_qty"] - s["undeducted_qty"])

        # 3. Batch License Available Seats
        license_avail = {}
        if license_ids:
            from django.db.models import Count

            from licenses.models import License

            licenses = (
                License.objects.filter(id__in=license_ids)
                .annotate(assigned_count=Count("assignments"))
                .values("id", "seats", "assigned_count")
            )
            for l in licenses:
                license_avail[l["id"]] = max(0, l["seats"] - l["assigned_count"])

        # 4. Batch Consumable Available Qty
        consumable_avail = {}
        if consumable_ids:
            from django.db.models import IntegerField, OuterRef, Subquery, Sum

            from ..models import ConsumableAssignment, ConsumableStock

            # Independent Subqueries per reverse relation (see accessory block):
            # avoids the stocks x consumptions cartesian-product double-count.
            stock_sub = (
                ConsumableStock.objects.filter(consumable=OuterRef("pk"))
                .order_by()
                .values("consumable")
                .annotate(total=Sum("qty"))
                .values("total")
            )
            undeducted_sub = (
                ConsumableAssignment.objects.filter(consumable=OuterRef("pk"), from_location__isnull=True)
                .order_by()
                .values("consumable")
                .annotate(total=Sum("qty"))
                .values("total")
            )
            stocks = (
                Consumable.objects.filter(id__in=consumable_ids)
                .annotate(
                    total_qty=Coalesce(Subquery(stock_sub, output_field=IntegerField()), 0),
                    undeducted_qty=Coalesce(Subquery(undeducted_sub, output_field=IntegerField()), 0),
                )
                .values("id", "total_qty", "undeducted_qty")
            )
            for s in stocks:
                consumable_avail[s["id"]] = max(0, s["total_qty"] - s["undeducted_qty"])

        for item in items:
            avail = 0
            if item.asset_type_id:
                avail = asset_counts.get(item.asset_type_id, 0)
                if avail < 1:
                    all_available = False
            elif item.accessory_id:
                avail = accessory_avail.get(item.accessory_id, 0)
                if avail < item.qty:
                    all_available = False
            elif item.license_id:
                avail = license_avail.get(item.license_id, 0)
                if avail < 1:
                    all_available = False
            elif item.consumable_id:
                avail = consumable_avail.get(item.consumable_id, 0)
                if avail < item.qty:
                    all_available = False

            items_with_availability.append(
                {
                    "item": item,
                    "available_count": avail,
                    "is_available": (avail >= (item.qty if (item.accessory or item.consumable) else 1)),
                }
            )

        context["items_with_availability"] = items_with_availability
        context["all_available"] = all_available
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
            form = self.get_form()
            return render(request, self.error_partial, self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

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
