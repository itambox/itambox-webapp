from django_filters.rest_framework import DjangoFilterBackend
from itambox.api.viewsets import ITAMBoxModelViewSet
from core.managers import get_current_tenant
from compliance.models import CustodyReceipt, AuditSession, AssetAudit
from assets.models import AssetMaintenance
from compliance.filters import CustodyReceiptFilterSet, AssetMaintenanceFilterSet, AuditSessionFilterSet, AssetAuditFilterSet
from .serializers import CustodyReceiptSerializer, AssetMaintenanceSerializer, AuditSessionSerializer, AssetAuditSerializer


def _scope_by_asset_tenant(queryset):
    """Tenant-scope a queryset that derives its tenant through `asset.tenant`.

    These models (CustodyReceipt, AssetAudit, AssetMaintenance) have no direct
    `tenant` field, so StrictTenantPermission cannot enforce a boundary and the
    default manager is not tenant-scoped — without this filter the list/detail
    endpoints return every tenant's rows. Fail closed when no tenant is bound.
    """
    tenant = get_current_tenant()
    if tenant is None:
        return queryset.none()
    return queryset.filter(asset__tenant=tenant)


class CustodyReceiptViewSet(ITAMBoxModelViewSet):
    queryset = CustodyReceipt.objects.select_related('asset', 'holder').all()
    serializer_class = CustodyReceiptSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustodyReceiptFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())


class AssetMaintenanceViewSet(ITAMBoxModelViewSet):
    queryset = AssetMaintenance.objects.select_related('asset').all()
    serializer_class = AssetMaintenanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetMaintenanceFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())


class AuditSessionViewSet(ITAMBoxModelViewSet):
    queryset = AuditSession.objects.select_related('location', 'created_by').all()
    serializer_class = AuditSessionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditSessionFilterSet

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AssetAuditViewSet(ITAMBoxModelViewSet):
    queryset = AssetAudit.objects.select_related(
        'asset', 'auditor', 'location', 'status', 'session'
    ).all()
    serializer_class = AssetAuditSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetAuditFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())

    def perform_create(self, serializer):
        serializer.save(auditor=self.request.user)

