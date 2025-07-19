from django.db import models
from django_filters.rest_framework import DjangoFilterBackend

from core.api.viewsets import AssetBoxModelViewSet, AssetBoxReadOnlyModelViewSet
from assets.models import Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware
from assets.filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet
from .serializers import (
    AssetSerializer, AssetRoleSerializer, ManufacturerSerializer, AssetTypeSerializer,
    InstalledSoftwareSerializer
)


class AssetViewSet(AssetBoxModelViewSet):
    queryset = Asset.objects.select_related('asset_role', 'asset_type__manufacturer', 'location')
    serializer_class = AssetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetFilterSet


class AssetRoleViewSet(AssetBoxModelViewSet):
    queryset = AssetRole.objects.annotate(
        asset_count=models.Count('asset')
    )
    serializer_class = AssetRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRoleFilterSet


class ManufacturerViewSet(AssetBoxModelViewSet):
    queryset = Manufacturer.objects.annotate(
        asset_count=models.Count('asset_types__assets')
    )
    serializer_class = ManufacturerSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ManufacturerFilterSet


class InstalledSoftwareViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = InstalledSoftware.objects.select_related(
        'asset', 'software', 'software__manufacturer'
    ).all()
    serializer_class = InstalledSoftwareSerializer
    filterset_fields = ['asset_id', 'software_id', 'software__manufacturer_id', 'version_detected']
    search_fields = ['asset__name', 'software__name', 'version_detected']
