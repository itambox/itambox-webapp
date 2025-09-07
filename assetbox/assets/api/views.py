from django.db import models
from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend

from core.api.viewsets import AssetBoxModelViewSet, AssetBoxReadOnlyModelViewSet
from assets.models import (
    Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware,
    StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence,
    ActivityLog, AssetAssignment, AuditSession, AssetAudit
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
    AssetTagSequenceSerializer, ActivityLogSerializer, AssetAssignmentSerializer,
    AuditSessionSerializer, AssetAuditSerializer
)


class AssetViewSet(AssetBoxModelViewSet):
    queryset = Asset.objects.select_related('asset_role', 'asset_type__manufacturer', 'location').prefetch_related(
        Prefetch('assignments', queryset=AssetAssignment.objects.filter(is_active=True), to_attr='_active_assignments')
    )
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


class AssetTypeViewSet(AssetBoxModelViewSet):
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class = AssetTypeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTypeFilterSet


class StatusLabelViewSet(AssetBoxModelViewSet):
    queryset = StatusLabel.objects.prefetch_related('tags').all()
    serializer_class = StatusLabelSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = StatusLabelFilterSet


class DepreciationViewSet(AssetBoxModelViewSet):
    queryset = Depreciation.objects.all()
    serializer_class = DepreciationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DepreciationFilterSet


class SupplierViewSet(AssetBoxModelViewSet):
    queryset = Supplier.objects.prefetch_related('tags').all()
    serializer_class = SupplierSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SupplierFilterSet


class CategoryViewSet(AssetBoxModelViewSet):
    queryset = Category.objects.prefetch_related('tags').all()
    serializer_class = CategorySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CategoryFilterSet


class AssetRequestViewSet(AssetBoxModelViewSet):
    queryset = AssetRequest.objects.select_related(
        'requester', 'asset', 'asset_type__manufacturer', 'responded_by'
    ).prefetch_related('tags').all()
    serializer_class = AssetRequestSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRequestFilterSet


class AssetTagSequenceViewSet(AssetBoxModelViewSet):
    queryset = AssetTagSequence.objects.all()
    serializer_class = AssetTagSequenceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTagSequenceFilterSet


class ActivityLogViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = ActivityLog.objects.select_related('asset', 'user').all()
    serializer_class = ActivityLogSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'user_id', 'action']


class AssetAssignmentViewSet(AssetBoxModelViewSet):
    queryset = AssetAssignment.objects.select_related(
        'asset', 'checked_out_by', 'checked_in_by', 'assigned_to_content_type'
    ).prefetch_related('tags')
    serializer_class = AssetAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'is_active', 'checked_out_by_id']


class AuditSessionViewSet(AssetBoxModelViewSet):
    queryset = AuditSession.objects.select_related('location', 'created_by').all()
    serializer_class = AuditSessionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditSessionFilterSet

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AssetAuditViewSet(AssetBoxModelViewSet):
    queryset = AssetAudit.objects.select_related(
        'session', 'asset', 'auditor', 'location', 'status'
    ).all()
    serializer_class = AssetAuditSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetAuditFilterSet

    def perform_create(self, serializer):
        serializer.save(auditor=self.request.user)

