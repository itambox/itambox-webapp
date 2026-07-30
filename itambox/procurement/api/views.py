from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.response import Response

from itambox.api.openapi import MATURITY_EXTENSION
from itambox.api.permissions import StrictTenantPermission, TokenPermissions
from itambox.api.viewsets import ITAMBoxModelViewSet
from itambox.capabilities import STABLE
from procurement.filters import ContractFilterSet, PurchaseOrderFilterSet
from procurement.models import Contract, PurchaseOrder, PurchaseOrderLine
from procurement.services import (
    approve_purchase_order,
    cancel_purchase_order,
    order_purchase_order,
    receive_purchase_order,
    reopen_purchase_order,
)

from .serializers import (
    ContractSerializer,
    PurchaseOrderActionResponseSerializer,
    PurchaseOrderLineSerializer,
    PurchaseOrderReceiveSerializer,
    PurchaseOrderSerializer,
)


class PurchaseOrderActionPermissions(TokenPermissions):
    _current_action = None

    def get_required_permissions(self, method, model):
        if self._current_action in {"approve", "receive"}:
            return [f"{model._meta.app_label}.{self._current_action}_{model._meta.model_name}"]
        if self._current_action in {"order", "cancel", "reopen"}:
            method = "PATCH"
        return super().get_required_permissions(method, model)

    def has_permission(self, request, view):
        self._current_action = getattr(view, "action", None)
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        self._current_action = getattr(view, "action", None)
        return super().has_object_permission(request, view, obj)


class ContractViewSet(ITAMBoxModelViewSet):
    """
    CRUD API for Contracts.

    Tenant scoping is handled automatically:
    - `TenantScopingSoftDeleteManager` on `Contract.objects` filters to the
      active tenant at query time (via `BaseViewSet.get_queryset` calling
      `filter_by_tenant()`).
    - `StrictTenantPermission` enforces the boundary at object-level on
      detail endpoints.
    """

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Contract.objects.select_related(
        "tenant",
        "supplier",
        "purchase_order",
        "cost_center",
    ).prefetch_related("assets")
    serializer_class = ContractSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContractFilterSet


class PurchaseOrderViewSet(ITAMBoxModelViewSet):
    """
    CRUD API for Purchase Orders (with nested read-only line items).

    Tenant scoping is handled automatically:
    - `TenantScopingSoftDeleteManager` on `PurchaseOrder.objects` filters to
      the active tenant at query time (via `BaseViewSet.get_queryset` calling
      `filter_by_tenant()`).
    - `StrictTenantPermission` enforces the boundary at object-level on
      detail endpoints.
    """

    permission_classes = [PurchaseOrderActionPermissions, StrictTenantPermission]
    queryset = PurchaseOrder.objects.select_related(
        "tenant",
        "supplier",
        "destination_location",
        "created_by",
    ).prefetch_related("lines")
    serializer_class = PurchaseOrderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = PurchaseOrderFilterSet

    @extend_schema(
        request=None,
        responses=PurchaseOrderActionResponseSerializer,
        description="Approve this Purchase Order through the sanctioned lifecycle transition.",
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        purchase_order = self.get_object()
        result = approve_purchase_order(purchase_order, user=request.user, request=request)
        return Response(result)

    @extend_schema(
        request=None,
        responses=PurchaseOrderActionResponseSerializer,
        description="Mark this approved Purchase Order as ordered through the sanctioned lifecycle transition.",
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def order(self, request, pk=None):
        purchase_order = self.get_object()
        result = order_purchase_order(purchase_order, user=request.user, request=request)
        return Response(result)

    @extend_schema(
        request=PurchaseOrderReceiveSerializer,
        responses=PurchaseOrderActionResponseSerializer,
        description="Receive quantities for this Purchase Order through the sanctioned lifecycle transition.",
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"], serializer_class=PurchaseOrderReceiveSerializer)
    def receive(self, request, pk=None):
        purchase_order = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "purchase_order": purchase_order},
        )
        serializer.is_valid(raise_exception=True)
        receive_purchase_order(purchase_order, serializer.validated_data["line_quantities"])
        return Response({"message": f"Items received for Purchase Order {purchase_order.order_number}."})

    @extend_schema(
        request=None,
        responses=PurchaseOrderActionResponseSerializer,
        description="Cancel this Purchase Order through the sanctioned lifecycle transition.",
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        purchase_order = self.get_object()
        result = cancel_purchase_order(purchase_order, user=request.user, request=request)
        return Response(result)

    @extend_schema(
        request=None,
        responses=PurchaseOrderActionResponseSerializer,
        description="Reopen this cancelled Purchase Order through the sanctioned lifecycle transition.",
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        purchase_order = self.get_object()
        result = reopen_purchase_order(purchase_order, user=request.user, request=request)
        return Response(result)


class PurchaseOrderLineViewSet(ITAMBoxModelViewSet):
    """
    CRUD API for Purchase Order line items.

    Tenant scoping is handled automatically:
    - `TenantScopingSoftDeleteManager` on `PurchaseOrderLine.objects` filters
      to the active tenant at query time (via `BaseViewSet.get_queryset`
      calling `filter_by_tenant()`).
    - `StrictTenantPermission` enforces the boundary at object-level on
      detail endpoints.
    """

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = PurchaseOrderLine.objects.select_related(
        "tenant",
        "purchase_order",
        "asset_type",
        "component",
        "accessory",
        "consumable",
        "license",
    )
    serializer_class = PurchaseOrderLineSerializer
