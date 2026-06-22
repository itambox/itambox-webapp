from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import ValidationError as DRFValidationError
from itambox.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from licenses.models import License, LicenseSeatAssignment
from licenses.filters import LicenseFilterSet, LicenseSeatAssignmentFilterSet
from licenses.services import checkin_license_seat
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

    def perform_destroy(self, instance):
        # Route through checkin_license_seat() so the parent-License changelog entry
        # ("Checked in seat from …") is recorded on the API DELETE path, matching the
        # UI path.  The service soft-deletes the assignment and emits _log_change().
        try:
            checkin_license_seat(assignment=instance)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)



