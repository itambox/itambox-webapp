from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from components.models import ComponentType, ComponentInstance
from extras.api.serializers import TagSerializer


class ComponentTypeSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedManufacturerSerializer.Meta.model.objects.all(),
        source='manufacturer', write_only=True
    )
    tags = TagSerializer(many=True, read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = ComponentType
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_display', 'part_number', 'specs', 'description',
            'tags', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'category']


class ComponentInstanceSerializer(BaseModelSerializer):
    component_type = ComponentTypeSerializer(read_only=True)
    component_type_id = serializers.PrimaryKeyRelatedField(
        queryset=ComponentType.objects.all(),
        source='component_type', write_only=True
    )
    parent_asset = NestedAssetSerializer(read_only=True)
    parent_asset_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedAssetSerializer.Meta.model.objects.all(),
        source='parent_asset', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ComponentInstance
        fields = [
            'id', 'component_type', 'component_type_id', 'serial_number',
            'parent_asset', 'parent_asset_id', 'status', 'status_display',
            'purchase_date', 'purchase_cost', 'notes', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'component_type', 'serial_number', 'status']
