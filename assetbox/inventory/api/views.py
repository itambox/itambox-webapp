from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from inventory.models import Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, Kit, KitItem
from .serializers import (
    AccessorySerializer, AccessoryAssignmentSerializer,
    ConsumableSerializer, ConsumableAssignmentSerializer,
    KitSerializer, KitItemSerializer
)


class AccessoryViewSet(AssetBoxModelViewSet):
    queryset = Accessory.objects.select_related('manufacturer', 'tenant').prefetch_related('tags').all()
    serializer_class = AccessorySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['manufacturer_id', 'category', 'tenant_id']


class AccessoryAssignmentViewSet(AssetBoxModelViewSet):
    queryset = AccessoryAssignment.objects.select_related(
        'accessory__manufacturer', 'assigned_holder', 'assigned_location'
    ).all()
    serializer_class = AccessoryAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['accessory_id', 'assigned_holder_id', 'assigned_location_id']


class ConsumableViewSet(AssetBoxModelViewSet):
    queryset = Consumable.objects.select_related('manufacturer', 'tenant').prefetch_related('tags').all()
    serializer_class = ConsumableSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['manufacturer_id', 'category', 'tenant_id']


class ConsumableAssignmentViewSet(AssetBoxModelViewSet):
    queryset = ConsumableAssignment.objects.select_related(
        'consumable__manufacturer', 'assigned_holder', 'assigned_location'
    ).all()
    serializer_class = ConsumableAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['consumable_id', 'assigned_holder_id', 'assigned_location_id']


class KitViewSet(AssetBoxModelViewSet):
    queryset = Kit.objects.select_related('tenant').prefetch_related('items', 'tags').all()
    serializer_class = KitSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['tenant_id']


class KitItemViewSet(AssetBoxModelViewSet):
    queryset = KitItem.objects.select_related(
        'kit', 'asset_type__manufacturer', 'accessory__manufacturer'
    ).all()
    serializer_class = KitItemSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['kit_id', 'asset_type_id', 'accessory_id']
