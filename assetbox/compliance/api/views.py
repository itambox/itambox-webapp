from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from compliance.models import CustodyReceipt, AssetMaintenance
from .serializers import CustodyReceiptSerializer, AssetMaintenanceSerializer


class CustodyReceiptViewSet(AssetBoxModelViewSet):
    queryset = CustodyReceipt.objects.select_related('asset', 'holder').all()
    serializer_class = CustodyReceiptSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'holder_id', 'acceptance_status', 'accepted']


class AssetMaintenanceViewSet(AssetBoxModelViewSet):
    queryset = AssetMaintenance.objects.select_related('asset').all()
    serializer_class = AssetMaintenanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'maintenance_type', 'start_date', 'completion_date']
