from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet, AssetBoxReadOnlyModelViewSet
from organization.models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, AssetHolderAssignment, Contact, ContactRole, ContactAssignment
)
from organization.filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet,
    ContactFilterSet, ContactRoleFilterSet
)
from .serializers import (
    SiteSerializer, RegionSerializer, SiteGroupSerializer, LocationSerializer,
    TenantSerializer, TenantGroupSerializer, AssetHolderSerializer, AssetHolderAssignmentSerializer,
    ContactSerializer, ContactRoleSerializer, ContactAssignmentSerializer
)


class SiteViewSet(AssetBoxModelViewSet):
    queryset = Site.objects.select_related('region', 'group', 'tenant')
    serializer_class = SiteSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteFilterSet


class RegionViewSet(AssetBoxModelViewSet):
    queryset = Region.objects.select_related('parent').prefetch_related('sites')
    serializer_class = RegionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = RegionFilterSet


class SiteGroupViewSet(AssetBoxModelViewSet):
    queryset = SiteGroup.objects.select_related('parent').prefetch_related('sites')
    serializer_class = SiteGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SiteGroupFilterSet


class LocationViewSet(AssetBoxModelViewSet):
    queryset = Location.objects.select_related('site', 'parent', 'tenant').prefetch_related('assets')
    serializer_class = LocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = LocationFilterSet


class TenantGroupViewSet(AssetBoxModelViewSet):
    queryset = TenantGroup.objects.select_related('parent').prefetch_related('tenants')
    serializer_class = TenantGroupSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantGroupFilterSet


class TenantViewSet(AssetBoxModelViewSet):
    queryset = Tenant.objects.select_related('group')
    serializer_class = TenantSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TenantFilterSet


class AssetHolderViewSet(AssetBoxModelViewSet):
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('assignments')
    serializer_class = AssetHolderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetHolderFilterSet


class AssetHolderAssignmentViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = AssetHolderAssignment.objects.select_related(
        'asset_holder', 'content_type'
    ).prefetch_related('assigned_object')
    serializer_class = AssetHolderAssignmentSerializer


class ContactViewSet(AssetBoxModelViewSet):
    queryset = Contact.objects.prefetch_related('tags').all()
    serializer_class = ContactSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactFilterSet


class ContactRoleViewSet(AssetBoxModelViewSet):
    queryset = ContactRole.objects.all()
    serializer_class = ContactRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ContactRoleFilterSet


class ContactAssignmentViewSet(AssetBoxModelViewSet):
    queryset = ContactAssignment.objects.select_related(
        'contact', 'role', 'content_type'
    ).prefetch_related('contact__tags')
    serializer_class = ContactAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['contact_id', 'role_id', 'content_type_id', 'object_id']
