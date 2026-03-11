from django_filters.rest_framework import DjangoFilterBackend
from core.api.permissions import TokenPermissions, StrictTenantPermission
from core.api.viewsets import AssetBoxModelViewSet, AssetBoxReadOnlyModelViewSet
from organization.models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, AssetHolderAssignment, Contact, ContactRole, ContactAssignment
)
from organization.filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet,
    ContactFilterSet, ContactRoleFilterSet, AssetHolderAssignmentFilterSet,
    ContactAssignmentFilterSet
)
from .serializers import (
    SiteSerializer, RegionSerializer, SiteGroupSerializer, LocationSerializer,
    TenantSerializer, TenantGroupSerializer, AssetHolderSerializer, AssetHolderAssignmentSerializer,
    ContactSerializer, ContactRoleSerializer, ContactAssignmentSerializer
)


class SiteViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Site.objects.select_related('region', 'group', 'tenant')
    serializer_class = SiteSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteFilterSet


class RegionViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Region.objects.select_related('parent').prefetch_related('sites')
    serializer_class = RegionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = RegionFilterSet


class SiteGroupViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = SiteGroup.objects.select_related('parent').prefetch_related('sites')
    serializer_class = SiteGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteGroupFilterSet


class LocationViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Location.objects.select_related('site', 'parent', 'tenant').prefetch_related('assets')
    serializer_class = LocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LocationFilterSet


class TenantGroupViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = TenantGroup.objects.select_related('parent').prefetch_related('tenants')
    serializer_class = TenantGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantGroupFilterSet


class TenantViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Tenant.objects.select_related('group')
    serializer_class = TenantSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantFilterSet


class AssetHolderViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('assignments')
    serializer_class = AssetHolderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetHolderFilterSet


class AssetHolderAssignmentViewSet(AssetBoxReadOnlyModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetHolderAssignment.objects.select_related(
        'asset_holder', 'content_type'
    ).prefetch_related('assigned_object')
    serializer_class = AssetHolderAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetHolderAssignmentFilterSet



class ContactViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Contact.objects.prefetch_related('tags').all()
    serializer_class = ContactSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactFilterSet


class ContactRoleViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ContactRole.objects.all()
    serializer_class = ContactRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactRoleFilterSet


class ContactAssignmentViewSet(AssetBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ContactAssignment.objects.select_related(
        'contact', 'role', 'content_type'
    ).prefetch_related('contact__tags')
    serializer_class = ContactAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactAssignmentFilterSet

