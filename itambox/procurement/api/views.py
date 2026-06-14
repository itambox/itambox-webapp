from django_filters.rest_framework import DjangoFilterBackend

from itambox.api.permissions import TokenPermissions, StrictTenantPermission
from itambox.api.viewsets import ITAMBoxModelViewSet

from procurement.models import Contract
from procurement.filters import ContractFilterSet
from .serializers import ContractSerializer


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
