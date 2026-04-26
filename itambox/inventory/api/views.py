from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from core.api.permissions import TokenPermissions, StrictTenantPermission
from core.api.viewsets import ITAMBoxModelViewSet
from inventory.models import (
    Accessory, AccessoryStock, AccessoryAssignment,
    Consumable, ConsumableStock, ConsumableAssignment,
    Kit, KitItem
)
from inventory.filters import (
    AccessoryFilterSet, AccessoryStockFilterSet, AccessoryAssignmentFilterSet,
    ConsumableFilterSet, ConsumableStockFilterSet, ConsumableAssignmentFilterSet,
    KitFilterSet, KitItemFilterSet
)
from .serializers import (
    AccessorySerializer, AccessoryStockSerializer, AccessoryAssignmentSerializer,
    ConsumableSerializer, ConsumableStockSerializer, ConsumableAssignmentSerializer,
    KitSerializer, KitItemSerializer
)


class AccessoryViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Accessory.objects.select_related('manufacturer', 'tenant').prefetch_related('tags').all()
    serializer_class = AccessorySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AccessoryFilterSet


class AccessoryStockViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AccessoryStock.objects.select_related('accessory', 'location').all()
    serializer_class = AccessoryStockSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AccessoryStockFilterSet


class AccessoryAssignmentViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AccessoryAssignment.objects.select_related(
        'accessory__manufacturer', 'assigned_holder', 'assigned_location', 'from_location'
    ).all()
    serializer_class = AccessoryAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AccessoryAssignmentFilterSet


class ConsumableViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Consumable.objects.select_related('manufacturer', 'tenant').prefetch_related('tags').all()
    serializer_class = ConsumableSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ConsumableFilterSet


class ConsumableStockViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ConsumableStock.objects.select_related('consumable', 'location').all()
    serializer_class = ConsumableStockSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ConsumableStockFilterSet


class ConsumableAssignmentViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ConsumableAssignment.objects.select_related(
        'consumable__manufacturer', 'assigned_holder', 'assigned_location', 'from_location'
    ).all()
    serializer_class = ConsumableAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ConsumableAssignmentFilterSet


class KitViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Kit.objects.select_related('tenant').prefetch_related('items', 'tags').all()
    serializer_class = KitSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = KitFilterSet


class KitItemViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = KitItem.objects.select_related(
        'kit', 'asset_type__manufacturer', 'accessory__manufacturer'
    ).all()
    serializer_class = KitItemSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = KitItemFilterSet

