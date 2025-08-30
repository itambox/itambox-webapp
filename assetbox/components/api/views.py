from django_filters.rest_framework import DjangoFilterBackend
from core.api.viewsets import AssetBoxModelViewSet
from components.models import ComponentType, ComponentInstance
from .serializers import ComponentTypeSerializer, ComponentInstanceSerializer


class ComponentTypeViewSet(AssetBoxModelViewSet):
    queryset = ComponentType.objects.select_related('manufacturer').prefetch_related('tags').all()
    serializer_class = ComponentTypeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['manufacturer_id', 'category']


class ComponentInstanceViewSet(AssetBoxModelViewSet):
    queryset = ComponentInstance.objects.select_related(
        'component_type__manufacturer', 'parent_asset'
    ).prefetch_related('tags').all()
    serializer_class = ComponentInstanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ['component_type_id', 'parent_asset_id', 'status', 'serial_number']
