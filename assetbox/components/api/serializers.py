from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer, NestedAssetSerializer
from components.models import Component, ComponentStock, ComponentAllocation
from assets.models import Category, Asset
from organization.models import Location
from organization.api.serializers import NestedLocationSerializer
from extras.api.serializers import TagSerializer


class ComponentSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedManufacturerSerializer.Meta.model.objects.all(),
        source='manufacturer', write_only=True
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(applies_to__component=True),
        write_only=True
    )
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    total_allocated = serializers.IntegerField(read_only=True)
    available_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Component
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_id', 'category_name', 'part_number', 'specs',
            'min_stock_level', 'description', 'tags',
            'total_stock', 'total_allocated', 'available_stock',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'part_number']


class ComponentStockSerializer(BaseModelSerializer):
    component_name = serializers.CharField(source='component.name', read_only=True)
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        source='location', write_only=True
    )

    class Meta:
        model = ComponentStock
        fields = [
            'id', 'component', 'component_name', 'location', 'location_id',
            'qty', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'component_name', 'location', 'qty']


class ComponentAllocationSerializer(BaseModelSerializer):
    component_name = serializers.CharField(source='component.name', read_only=True)
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(),
        source='asset', write_only=True
    )
    from_location = NestedLocationSerializer(read_only=True)
    from_location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        source='from_location', write_only=True,
        required=False,
        allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = ComponentAllocation
        fields = [
            'id', 'component', 'component_name', 'asset', 'asset_id',
            'from_location', 'from_location_id',
            'qty_allocated', 'allocated_at', 'notes', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'component_name', 'asset', 'qty_allocated']
