from django_filters.rest_framework import DjangoFilterBackend

from itambox.api.permissions import TokenPermissions, StrictTenantPermission
from itambox.api.viewsets import ITAMBoxModelViewSet

from procurement.models import Contract, PurchaseOrder, PurchaseOrderLine
from procurement.filters import ContractFilterSet, PurchaseOrderFilterSet
from .serializers import ContractSerializer, PurchaseOrderSerializer, PurchaseOrderLineSerializer


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
        'tenant', 'supplier', 'purchase_order', 'cost_center',
    ).prefetch_related('assets')
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
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = PurchaseOrder.objects.select_related(
        'tenant', 'supplier', 'destination_location', 'created_by',
    ).prefetch_related('lines')
    serializer_class = PurchaseOrderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = PurchaseOrderFilterSet


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
        'tenant', 'purchase_order', 'asset_type', 'component',
        'accessory', 'consumable', 'license',
    )
    serializer_class = PurchaseOrderLineSerializer
