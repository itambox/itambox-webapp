from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxReadOnlyModelViewSet
from licenses.models import License, LicenseSeatAssignment
from licenses.filters import LicenseFilterSet
from .serializers import LicenseSerializer, LicenseSeatAssignmentSerializer


class LicenseViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = License.objects.select_related(
        'software__manufacturer'
    ).prefetch_related('tags').all()
    serializer_class = LicenseSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LicenseFilterSet


class LicenseSeatAssignmentViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = LicenseSeatAssignment.objects.select_related(
        'license__software', 'asset', 'assigned_holder'
    ).all()
    serializer_class = LicenseSeatAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['license_id', 'asset_id', 'assigned_holder_id']
