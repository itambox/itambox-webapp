from django.db import models
from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from itambox.api.permissions import TokenPermissions, StrictTenantPermission

from itambox.api.viewsets import ITAMBoxModelViewSet
from assets.models import (
    Asset, AssetRole, Manufacturer, AssetType,
    StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence,
    AssetAssignment, AssetDisposal,
)
from assets.filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet,
    AssetTypeFilterSet, StatusLabelFilterSet, DepreciationFilterSet,
    SupplierFilterSet, CategoryFilterSet, AssetRequestFilterSet, AssetTagSequenceFilterSet,
)
from .serializers import (
    AssetSerializer, AssetRoleSerializer, ManufacturerSerializer, AssetTypeSerializer,
    StatusLabelSerializer, DepreciationSerializer,
    SupplierSerializer, CategorySerializer, AssetRequestSerializer,
    AssetTagSequenceSerializer, AssetAssignmentSerializer,
    AssetCheckOutAPISerializer,
    AssetCheckInAPISerializer,
    AssetDisposalSerializer,
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

    @extend_schema(
        request=AssetCheckOutAPISerializer,
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}
    )
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
            notes=serializer.validated_data.get('notes', ''),
            status=serializer.validated_data.get('status_id')
        )
        
        return Response(
            {"status": "success", "message": f"Asset checked out to {target}."},
            status=status.HTTP_200_OK
        )

    @extend_schema(
        request=AssetCheckInAPISerializer,
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}
    )
    @action(detail=True, methods=['post'], serializer_class=AssetCheckInAPISerializer)
    def checkin(self, request, pk=None):
        """
        API Action to check in an asset.
        """
        asset = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        checkin_asset(
            asset=asset,
            user=request.user,
            notes=serializer.validated_data.get('notes', ''),
            status=serializer.validated_data.get('status_id'),
            location=serializer.validated_data.get('location_id'),
            checkin_date=serializer.validated_data.get('checkin_date')
        )
        return Response(
            {"status": "success", "message": f"Asset {asset.asset_tag} checked in successfully."},
            status=status.HTTP_200_OK
        )


class AssetRoleViewSet(ITAMBoxModelViewSet):
    queryset = AssetRole.objects.annotate(
        asset_count=models.Count('assets')
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
    filterset_fields = ['asset_id', 'is_active', 'checked_out_by_id', 'assigned_user_id']


class AssetDisposalViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetDisposal.objects.select_related(
        'asset', 'asset__asset_type__manufacturer', 'asset__tenant',
    )
    serializer_class = AssetDisposalSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['asset_id', 'disposal_method', 'data_sanitization_method', 'weee_compliant']



