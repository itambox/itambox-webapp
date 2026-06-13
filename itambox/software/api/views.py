from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.permissions import BasePermission

from itambox.api.permissions import TokenPermissions
from itambox.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from software.filters import SoftwareFilterSet
from software.models import Software, InstalledSoftware
from .serializers import SoftwareSerializer, InstalledSoftwareSerializer


class SoftwareViewSet(ITAMBoxModelViewSet):
    permission_classes: list[type[BasePermission]] = [TokenPermissions]
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class: type[SoftwareSerializer] = SoftwareSerializer
    filter_backends: tuple = (DjangoFilterBackend,)
    filterset_class: type[SoftwareFilterSet] = SoftwareFilterSet


class InstalledSoftwareViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = InstalledSoftware.objects.select_related(
        'asset', 'software', 'software__manufacturer'
    ).all()
    serializer_class = InstalledSoftwareSerializer
    filterset_fields = ['asset_id', 'software_id', 'software__manufacturer_id', 'version_detected']
    search_fields = ['asset__name', 'software__name', 'version_detected']

    def get_queryset(self):
        # InstalledSoftware has no `tenant` field of its own, so the inherited
        # filter_by_tenant() is a no-op and the base queryset would expose every
        # tenant's records. Scope through the (tenant-owned) Asset relationship,
        # failing closed when no tenant is active.
        from core.managers import get_current_tenant

        qs = super().get_queryset()
        active_tenant = get_current_tenant()
        if active_tenant is None:
            return qs.none()
        return qs.filter(asset__tenant=active_tenant)
