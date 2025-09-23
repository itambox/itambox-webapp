from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from components.models import Component, ComponentStock, ComponentAllocation
from components.filters import ComponentFilterSet, ComponentStockFilterSet, ComponentAllocationFilterSet
from .serializers import (
    ComponentSerializer, ComponentStockSerializer, ComponentAllocationSerializer
)


class ComponentViewSet(AssetBoxModelViewSet):
    queryset = Component.objects.select_related('manufacturer', 'category').prefetch_related('tags').all()
    serializer_class = ComponentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentFilterSet


class ComponentStockViewSet(AssetBoxModelViewSet):
    queryset = ComponentStock.objects.select_related('component', 'location').all()
    serializer_class = ComponentStockSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentStockFilterSet


class ComponentAllocationViewSet(AssetBoxModelViewSet):
    queryset = ComponentAllocation.objects.select_related('component', 'asset').prefetch_related('tags').all()
    serializer_class = ComponentAllocationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ComponentAllocationFilterSet

