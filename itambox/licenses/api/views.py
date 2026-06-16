from django_filters.rest_framework import DjangoFilterBackend
from itambox.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from licenses.models import License, LicenseSeatAssignment
from licenses.filters import LicenseFilterSet, LicenseSeatAssignmentFilterSet
from .serializers import LicenseSerializer, LicenseSeatAssignmentSerializer
from itambox.api.permissions import TokenPermissions, StrictTenantPermission


class LicenseViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = License.objects.with_counts().select_related(
        'software__manufacturer'
    ).prefetch_related('tags').all()
    serializer_class = LicenseSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LicenseFilterSet


class LicenseSeatAssignmentViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = LicenseSeatAssignment.objects.select_related(
        'license__software', 'asset', 'assigned_holder'
    ).all()
    serializer_class = LicenseSeatAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LicenseSeatAssignmentFilterSet



