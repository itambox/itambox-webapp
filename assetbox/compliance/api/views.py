from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from compliance.models import CustodyReceipt, AssetMaintenance
from compliance.filters import CustodyReceiptFilterSet, AssetMaintenanceFilterSet
from .serializers import CustodyReceiptSerializer, AssetMaintenanceSerializer


class CustodyReceiptViewSet(AssetBoxModelViewSet):
    queryset = CustodyReceipt.objects.select_related('asset', 'holder').all()
    serializer_class = CustodyReceiptSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustodyReceiptFilterSet


class AssetMaintenanceViewSet(AssetBoxModelViewSet):
    queryset = AssetMaintenance.objects.select_related('asset').all()
    serializer_class = AssetMaintenanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetMaintenanceFilterSet

