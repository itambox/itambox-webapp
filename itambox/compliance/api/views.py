from django_filters.rest_framework import DjangoFilterBackend
from itambox.api.viewsets import ITAMBoxModelViewSet
from compliance.models import CustodyReceipt, AuditSession, AssetAudit
from assets.models import AssetMaintenance
from compliance.filters import CustodyReceiptFilterSet, AssetMaintenanceFilterSet, AuditSessionFilterSet, AssetAuditFilterSet
from .serializers import CustodyReceiptSerializer, AssetMaintenanceSerializer, AuditSessionSerializer, AssetAuditSerializer


class CustodyReceiptViewSet(ITAMBoxModelViewSet):
    queryset = CustodyReceipt.objects.select_related('asset', 'holder').all()
    serializer_class = CustodyReceiptSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustodyReceiptFilterSet


class AssetMaintenanceViewSet(ITAMBoxModelViewSet):
    queryset = AssetMaintenance.objects.select_related('asset').all()
    serializer_class = AssetMaintenanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetMaintenanceFilterSet


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

    def perform_create(self, serializer):
        serializer.save(auditor=self.request.user)

