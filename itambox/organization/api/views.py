from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from itambox.api.permissions import TokenPermissions, StrictTenantPermission
from itambox.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from organization.models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, Contact, ContactRole, ContactAssignment, CostCenter,
)
from organization.filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet,
    ContactFilterSet, ContactRoleFilterSet, ContactAssignmentFilterSet,
    CostCenterFilterSet,
)
from .serializers import (
    SiteSerializer, RegionSerializer, SiteGroupSerializer, LocationSerializer,
    TenantSerializer, TenantGroupSerializer, AssetHolderSerializer,
    ContactSerializer, ContactRoleSerializer, ContactAssignmentSerializer,
    CostCenterSerializer,
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

    def get_queryset(self):
        # ContactAssignment is a generic-FK assignment model with no direct or
        # relational `tenant` field (Contact itself is not tenant-scoped), so it
        # uses the unscoped default manager and filter_by_tenant() is a no-op.
        # Scope the LIST per content type: a generic FK cannot be ORM-joined to a
        # tenant column, so for every target ContentType present we collect that
        # model's object ids belonging to the active tenant and OR the matches
        # together. Object-level (detail/mutation) boundary is enforced
        # separately by StrictTenantPermission via the model's `tenant` property.
        qs = super().get_queryset()

        if self.request.user.is_superuser:
            return qs

        from core.managers import get_current_tenant
        from django.db.models import Q

        active_tenant = get_current_tenant()
        if active_tenant is None:
            # Authenticated non-superuser with no resolved tenant: fail closed.
            return qs.none()

        allowed = Q(pk__in=[])
        # Bound iteration to the content types actually referenced by existing
        # assignments rather than every ContentType in the install.
        content_type_ids = qs.values_list('content_type', flat=True).distinct()
        for ct in ContentType.objects.filter(pk__in=list(content_type_ids)):
            model_class = ct.model_class()
            if model_class is None:
                continue
            try:
                model_class._meta.get_field('tenant')
            except FieldDoesNotExist:
                # Target model has no tenant field (global/shared catalogue,
                # e.g. Manufacturer): such rows are not tenant-owned, so their
                # assignments stay visible to every tenant.
                allowed |= Q(content_type=ct)
                continue
            # Resolve visible ids through the model's default (tenant-scoped)
            # manager so soft-deletes and tenant scoping both apply.
            ids = list(
                model_class._default_manager.filter(tenant=active_tenant)
                .values_list('pk', flat=True)
            )
            allowed |= Q(content_type=ct, object_id__in=ids)

        return qs.filter(allowed)


class CostCenterViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = CostCenter.objects.select_related('tenant', 'parent').prefetch_related('children')
    serializer_class = CostCenterSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CostCenterFilterSet

