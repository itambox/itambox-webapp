# organization/api/views.py
from rest_framework import viewsets
# Import filter backend
from django_filters.rest_framework import DjangoFilterBackend
# Use app-relative imports for models within the same Django project
from organization.models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, AssetHolderAssignment
# Import FilterSets
from organization.filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet
)
from .serializers import (
    SiteSerializer, RegionSerializer, SiteGroupSerializer, LocationSerializer,
    TenantSerializer, TenantGroupSerializer, AssetHolderSerializer, AssetHolderAssignmentSerializer
)

# Inspired by NetBox API views

class SiteViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Sites.
    queryset = Site.objects.select_related('region', 'group', 'tenant') # Select related for nested serializers
    serializer_class = SiteSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteFilterSet
    # TODO: Add filtering, permissions etc. later

class RegionViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Regions.
    queryset = Region.objects.select_related('parent').prefetch_related('sites') # Select/Prefetch for nested/counts
    serializer_class = RegionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = RegionFilterSet
    # TODO: Add filtering, permissions etc. later

class SiteGroupViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Site Groups.
    queryset = SiteGroup.objects.select_related('parent').prefetch_related('sites') # Select/Prefetch for nested/counts
    serializer_class = SiteGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteGroupFilterSet

class LocationViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Locations.
    # Optimize for nested site, parent, tenant serializers and asset_count
    queryset = Location.objects.select_related('site', 'parent', 'tenant').prefetch_related('assets')
    serializer_class = LocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LocationFilterSet

class TenantGroupViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Tenant Groups.
    queryset = TenantGroup.objects.select_related('parent').prefetch_related('tenants') # Select/Prefetch for nested/counts
    serializer_class = TenantGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantGroupFilterSet

class TenantViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Tenants.
    queryset = Tenant.objects.select_related('group') # Optimize for nested group serializer
    serializer_class = TenantSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantFilterSet

class AssetHolderViewSet(viewsets.ModelViewSet):
    # API endpoint for managing Asset Holders.
    # Optimize for nested tenant serializer and assignment_count
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('assignments')
    serializer_class = AssetHolderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetHolderFilterSet

class AssetHolderAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    # Read-only endpoint for viewing assignments
    queryset = AssetHolderAssignment.objects.select_related(
        'asset_holder', 'content_type'
    ).prefetch_related('assigned_object')
    serializer_class = AssetHolderAssignmentSerializer
    # Add filtering if needed later
    # filter_backends = (DjangoFilterBackend,)
    # filterset_class = AssetHolderAssignmentFilterSet

# Add viewsets for SiteGroup, Location, Tenant, TenantGroup, AssetHolderAssignment below... 