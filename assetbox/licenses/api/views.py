from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet, AssetBoxReadOnlyModelViewSet
from licenses.models import License, LicenseSeatAssignment
from licenses.filters import LicenseFilterSet
from .serializers import LicenseSerializer, LicenseSeatAssignmentSerializer
from core.api.permissions import TokenPermissions, StrictTenantPermission


class LicenseViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = License.objects.select_related(
        'software__manufacturer'
    ).prefetch_related('tags').all()
    serializer_class = LicenseSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LicenseFilterSet


class LicenseSeatAssignmentViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = LicenseSeatAssignment.objects.select_related(
        'license__software', 'asset', 'assigned_holder'
    ).all()
    serializer_class = LicenseSeatAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['license_id', 'asset_id', 'assigned_holder_id']


