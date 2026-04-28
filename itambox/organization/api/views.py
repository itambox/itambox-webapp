from django_filters.rest_framework import DjangoFilterBackend
from core.api.permissions import TokenPermissions, StrictTenantPermission
from core.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from organization.models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, Contact, ContactRole, ContactAssignment
)
from organization.filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet,
    ContactFilterSet, ContactRoleFilterSet, ContactAssignmentFilterSet
)
from .serializers import (
    SiteSerializer, RegionSerializer, SiteGroupSerializer, LocationSerializer,
    TenantSerializer, TenantGroupSerializer, AssetHolderSerializer,
    ContactSerializer, ContactRoleSerializer, ContactAssignmentSerializer
)


class SiteViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Site.objects.select_related('region', 'group', 'tenant')
    serializer_class = SiteSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteFilterSet


class RegionViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Region.objects.select_related('parent').prefetch_related('sites')
    serializer_class = RegionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = RegionFilterSet


class SiteGroupViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = SiteGroup.objects.select_related('parent').prefetch_related('sites')
    serializer_class = SiteGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteGroupFilterSet


class LocationViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Location.objects.select_related('site', 'parent', 'tenant').prefetch_related('assets')
    serializer_class = LocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LocationFilterSet


class TenantGroupViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = TenantGroup.objects.select_related('parent').prefetch_related('tenants')
    serializer_class = TenantGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantGroupFilterSet


class TenantViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Tenant.objects.select_related('group')
    serializer_class = TenantSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantFilterSet


class AssetHolderViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('asset_assignments')
    serializer_class = AssetHolderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetHolderFilterSet




class ContactViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Contact.objects.prefetch_related('tags').all()
    serializer_class = ContactSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactFilterSet


class ContactRoleViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ContactRole.objects.all()
    serializer_class = ContactRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactRoleFilterSet


class ContactAssignmentViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ContactAssignment.objects.select_related(
        'contact', 'role', 'content_type'
    ).prefetch_related('contact__tags')
    serializer_class = ContactAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactAssignmentFilterSet

