from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from core.api.permissions import TokenPermissions, StrictTenantPermission
from core.api.viewsets import ITAMBoxModelViewSet
from inventory.models import (
    Accessory, AccessoryStock, AccessoryAssignment,
    Consumable, ConsumableStock, ConsumableAssignment,
    Kit, KitItem, Component, ComponentStock, ComponentAllocation
)
from inventory.filters import (
    AccessoryFilterSet, AccessoryStockFilterSet, AccessoryAssignmentFilterSet,
    ConsumableFilterSet, ConsumableStockFilterSet, ConsumableAssignmentFilterSet,
    KitFilterSet, KitItemFilterSet,
    ComponentFilterSet, ComponentStockFilterSet, ComponentAllocationFilterSet
)
from .serializers import (
    AccessorySerializer, AccessoryStockSerializer, AccessoryAssignmentSerializer,
    ConsumableSerializer, ConsumableStockSerializer, ConsumableAssignmentSerializer,
    KitSerializer, KitItemSerializer,
    ComponentSerializer, ComponentStockSerializer, ComponentAllocationSerializer
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


class ComponentViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Component.objects.select_related('manufacturer', 'tenant', 'category').prefetch_related('tags').all()
    serializer_class = ComponentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentFilterSet


class ComponentStockViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ComponentStock.objects.select_related('component', 'location').all()
    serializer_class = ComponentStockSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentStockFilterSet


class ComponentAllocationViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = ComponentAllocation.objects.select_related(
        'component__manufacturer', 'assigned_holder', 'assigned_location', 'assigned_asset', 'from_location'
    ).all()
    serializer_class = ComponentAllocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentAllocationFilterSet

