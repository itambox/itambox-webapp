# assets/api/views.py
from django.db.models import Count # Import Count
from rest_framework import viewsets
from django_filters.rest_framework import DjangoFilterBackend
from assets.models import Asset, AssetRole, Manufacturer
from assets.filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet
from .serializers import (
    AssetSerializer, AssetRoleSerializer, ManufacturerSerializer
)

class AssetViewSet(viewsets.ModelViewSet):
    # Add prefetch_related for nested serializers if needed later
    queryset = Asset.objects.select_related('asset_role', 'manufacturer', 'location')
    serializer_class = AssetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetFilterSet

class AssetRoleViewSet(viewsets.ModelViewSet):
    # Annotate the queryset to calculate asset_count
    queryset = AssetRole.objects.annotate(
        asset_count=Count('asset') # Assumes related_name is 'asset' or default 'asset_set'
    )
    serializer_class = AssetRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRoleFilterSet

class ManufacturerViewSet(viewsets.ModelViewSet):
    # Annotate the queryset to calculate asset_count following the new relationship
    queryset = Manufacturer.objects.annotate(
        asset_count=Count('asset_types__assets') # Corrected relation
    )
    serializer_class = ManufacturerSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ManufacturerFilterSet 