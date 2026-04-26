from django.db import models
from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from core.api.permissions import TokenPermissions, StrictTenantPermission

from core.api.viewsets import ITAMBoxModelViewSet, ITAMBoxReadOnlyModelViewSet
from assets.models import (
    Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware,
    StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence,
    AssetAssignment, AuditSession, AssetAudit
)
from assets.filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet,
    AssetTypeFilterSet, StatusLabelFilterSet, DepreciationFilterSet,
    SupplierFilterSet, CategoryFilterSet, AssetRequestFilterSet, AssetTagSequenceFilterSet,
    AuditSessionFilterSet, AssetAuditFilterSet
)
from .serializers import (
    AssetSerializer, AssetRoleSerializer, ManufacturerSerializer, AssetTypeSerializer,
    InstalledSoftwareSerializer, StatusLabelSerializer, DepreciationSerializer,
    SupplierSerializer, CategorySerializer, AssetRequestSerializer,
    AssetTagSequenceSerializer, AssetAssignmentSerializer,
    AuditSessionSerializer, AssetAuditSerializer, AssetCheckOutAPISerializer
)
from assets.services import checkout_asset, checkin_asset


class AssetViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Asset.objects.select_related('asset_role', 'asset_type__manufacturer', 'location').prefetch_related(
        Prefetch('assignments', queryset=AssetAssignment.objects.filter(is_active=True), to_attr='_active_assignments')
    )
    serializer_class = AssetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetFilterSet

    @action(detail=True, methods=['post'], serializer_class=AssetCheckOutAPISerializer)
    def checkout(self, request, pk=None):
        """
        API Action to check out an asset.
        """
        asset = self.get_object()
        serializer = self.get_serializer(data=request.data, context={'asset': asset})
        serializer.is_valid(raise_exception=True)
        
        target = checkout_asset(
            asset=asset,
            user=request.user,
            holder=serializer.validated_data.get('holder'),
            location=serializer.validated_data.get('location'),
            asset_target=serializer.validated_data.get('asset_target'),
            expected_checkin=serializer.validated_data.get('expected_checkin'),
            notes=serializer.validated_data.get('notes', '')
        )
        
        return Response(
            {"status": "success", "message": f"Asset checked out to {target}."},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def checkin(self, request, pk=None):
        """
        API Action to check in an asset.
        """
        asset = self.get_object()
        checkin_asset(asset, user=request.user, notes=request.data.get('notes', ''))
        return Response(
            {"status": "success", "message": f"Asset {asset.asset_tag} checked in successfully."},
            status=status.HTTP_200_OK
        )


class AssetRoleViewSet(ITAMBoxModelViewSet):
    queryset = AssetRole.objects.annotate(
        asset_count=models.Count('asset')
    )
    serializer_class = AssetRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRoleFilterSet


class ManufacturerViewSet(ITAMBoxModelViewSet):
    queryset = Manufacturer.objects.annotate(
        asset_count=models.Count('asset_types__assets')
    )
    serializer_class = ManufacturerSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ManufacturerFilterSet


class InstalledSoftwareViewSet(ITAMBoxReadOnlyModelViewSet):
    queryset = InstalledSoftware.objects.select_related(
        'asset', 'software', 'software__manufacturer'
    ).all()
    serializer_class = InstalledSoftwareSerializer
    filterset_fields = ['asset_id', 'software_id', 'software__manufacturer_id', 'version_detected']
    search_fields = ['asset__name', 'software__name', 'version_detected']


class AssetTypeViewSet(ITAMBoxModelViewSet):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class = AssetTypeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTypeFilterSet


class StatusLabelViewSet(ITAMBoxModelViewSet):
    queryset = StatusLabel.objects.prefetch_related('tags').all()
    serializer_class = StatusLabelSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = StatusLabelFilterSet


class DepreciationViewSet(ITAMBoxModelViewSet):
    queryset = Depreciation.objects.all()
    serializer_class = DepreciationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DepreciationFilterSet


class SupplierViewSet(ITAMBoxModelViewSet):
    queryset = Supplier.objects.prefetch_related('tags').all()
    serializer_class = SupplierSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SupplierFilterSet


class CategoryViewSet(ITAMBoxModelViewSet):
    queryset = Category.objects.prefetch_related('tags').all()
    serializer_class = CategorySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CategoryFilterSet


class AssetRequestViewSet(ITAMBoxModelViewSet):
    queryset = AssetRequest.objects.select_related(
        'requester', 'asset', 'asset_type__manufacturer', 'responded_by'
    ).prefetch_related('tags').all()
    serializer_class = AssetRequestSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRequestFilterSet


class AssetTagSequenceViewSet(ITAMBoxModelViewSet):
    queryset = AssetTagSequence.objects.all()
    serializer_class = AssetTagSequenceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTagSequenceFilterSet



class AssetAssignmentViewSet(ITAMBoxModelViewSet):
    queryset = AssetAssignment.objects.select_related(
        'asset', 'checked_out_by', 'checked_in_by', 'assigned_user', 'assigned_location', 'assigned_asset'
    ).prefetch_related('tags')
    serializer_class = AssetAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'is_active', 'checked_out_by_id']


class AuditSessionViewSet(ITAMBoxModelViewSet):
    queryset = AuditSession.objects.select_related('location', 'created_by').all()
    serializer_class = AuditSessionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditSessionFilterSet

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AssetAuditViewSet(ITAMBoxModelViewSet):
    queryset = AssetAudit.objects.select_related(
        'session', 'asset', 'auditor', 'location', 'status'
    ).all()
    serializer_class = AssetAuditSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetAuditFilterSet

    def perform_create(self, serializer):
        serializer.save(auditor=self.request.user)

