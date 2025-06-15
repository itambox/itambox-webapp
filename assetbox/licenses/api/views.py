from rest_framework import viewsets

from licenses.models import License, LicenseSeatAssignment
from .serializers import LicenseSerializer, LicenseSeatAssignmentSerializer

class LicenseViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Licenses."""
    queryset = License.objects.select_related(
        'software__manufacturer'
    ).prefetch_related('tags').all()
    serializer_class = LicenseSerializer
    # Add filters later if needed

class LicenseSeatAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for License Seat Assignments."""
    queryset = LicenseSeatAssignment.objects.select_related(
        'license__software', 'asset', 'assigned_holder'
    ).all()
    serializer_class = LicenseSeatAssignmentSerializer
    # Add filters later if needed 