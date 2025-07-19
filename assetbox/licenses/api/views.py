from core.api.viewsets import AssetBoxReadOnlyModelViewSet
from licenses.models import License, LicenseSeatAssignment
from .serializers import LicenseSerializer, LicenseSeatAssignmentSerializer


class LicenseViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = License.objects.select_related(
        'software__manufacturer'
    ).prefetch_related('tags').all()
    serializer_class = LicenseSerializer


class LicenseSeatAssignmentViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = LicenseSeatAssignment.objects.select_related(
        'license__software', 'asset', 'assigned_holder'
    ).all()
    serializer_class = LicenseSeatAssignmentSerializer
