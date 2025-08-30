from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer, NestedAssetTypeSerializer
from inventory.models import Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, Kit, KitItem
from organization.api.serializers import NestedTenantSerializer, AssetHolderSerializer, NestedLocationSerializer
from extras.api.serializers import TagSerializer


class NestedAccessorySerializer(BaseModelSerializer):
    class Meta:
        model = Accessory
        fields = ['id', 'name', 'manufacturer']
        brief_fields = ['id', 'name']


class AccessorySerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedManufacturerSerializer.Meta.model.objects.all(),
        source='manufacturer', write_only=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedTenantSerializer.Meta.model.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    remaining_qty = serializers.IntegerField(read_only=True)

    class Meta:
        model = Accessory
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_display', 'part_number',
            'qty', 'min_qty', 'remaining_qty',
            'allow_overallocate', 'notes', 'tags', 'tenant', 'tenant_id',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'category', 'qty', 'remaining_qty']


class AccessoryAssignmentSerializer(BaseModelSerializer):
    accessory = NestedAccessorySerializer(read_only=True)
    accessory_id = serializers.PrimaryKeyRelatedField(
        queryset=Accessory.objects.all(),
        source='accessory', write_only=True
    )
    assigned_holder = AssetHolderSerializer(read_only=True)
    assigned_holder_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetHolderSerializer.Meta.model.objects.all(),
        source='assigned_holder', write_only=True, required=False, allow_null=True
    )
    assigned_location = NestedLocationSerializer(read_only=True)
    assigned_location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='assigned_location', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = AccessoryAssignment
        fields = [
            'id', 'accessory', 'accessory_id',
            'assigned_holder', 'assigned_holder_id',
            'assigned_location', 'assigned_location_id',
            'qty', 'assigned_date', 'notes',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'accessory', 'assigned_holder', 'qty']


class NestedConsumableSerializer(BaseModelSerializer):
    class Meta:
        model = Consumable
        fields = ['id', 'name', 'manufacturer']
        brief_fields = ['id', 'name']


class ConsumableSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedManufacturerSerializer.Meta.model.objects.all(),
        source='manufacturer', write_only=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedTenantSerializer.Meta.model.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    remaining_qty = serializers.IntegerField(read_only=True)

    class Meta:
        model = Consumable
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_display', 'part_number',
            'qty', 'min_qty', 'remaining_qty',
            'allow_overallocate', 'notes', 'tags', 'tenant', 'tenant_id',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'category', 'qty', 'remaining_qty']


class ConsumableAssignmentSerializer(BaseModelSerializer):
    consumable = NestedConsumableSerializer(read_only=True)
    consumable_id = serializers.PrimaryKeyRelatedField(
        queryset=Consumable.objects.all(),
        source='consumable', write_only=True
    )
    assigned_holder = AssetHolderSerializer(read_only=True)
    assigned_holder_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetHolderSerializer.Meta.model.objects.all(),
        source='assigned_holder', write_only=True, required=False, allow_null=True
    )
    assigned_location = NestedLocationSerializer(read_only=True)
    assigned_location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='assigned_location', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = ConsumableAssignment
        fields = [
            'id', 'consumable', 'consumable_id',
            'assigned_holder', 'assigned_holder_id',
            'assigned_location', 'assigned_location_id',
            'qty', 'assigned_date', 'notes',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'consumable', 'assigned_holder', 'qty']


class KitItemSerializer(BaseModelSerializer):
    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedAssetTypeSerializer.Meta.model.objects.all(),
        source='asset_type', write_only=True, required=False, allow_null=True
    )
    accessory = NestedAccessorySerializer(read_only=True)
    accessory_id = serializers.PrimaryKeyRelatedField(
        queryset=Accessory.objects.all(),
        source='accessory', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = KitItem
        fields = [
            'id', 'kit', 'asset_type', 'asset_type_id',
            'accessory', 'accessory_id', 'license',
            'qty', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'asset_type', 'accessory', 'qty']


class KitSerializer(BaseModelSerializer):
    items = KitItemSerializer(many=True, read_only=True)
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedTenantSerializer.Meta.model.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Kit
        fields = [
            'id', 'name', 'description', 'items',
            'tenant', 'tenant_id', 'tags',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'description']
