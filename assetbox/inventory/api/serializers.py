from rest_framework import serializers
from core.api.base import BaseModelSerializer
from core.api.nested_serializers import NestedManufacturerSerializer, NestedAssetTypeSerializer
from inventory.models import (
    Accessory, AccessoryStock, AccessoryAssignment,
    Consumable, ConsumableStock, ConsumableAssignment,
    Kit, KitItem
)
from organization.api.serializers import NestedTenantSerializer, AssetHolderSerializer, NestedLocationSerializer
from extras.api.serializers import TagSerializer
from assets.models import Category


def _accessory_category_queryset():
    return Category.objects.filter(applies_to__contains={'accessory': True})

def _consumable_category_queryset():
    return Category.objects.filter(applies_to__contains={'consumable': True})


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
    category = serializers.SerializerMethodField(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=_accessory_category_queryset(),
        source='category', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedTenantSerializer.Meta.model.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    checked_out_qty = serializers.IntegerField(read_only=True)
    available = serializers.IntegerField(read_only=True)

    class Meta:
        model = Accessory
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_id', 'part_number',
            'total_stock', 'checked_out_qty', 'available',
            'min_qty', 'allow_overallocate', 'notes', 'tags',
            'tenant', 'tenant_id',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'category', 'available']

    def get_category(self, obj):
        if obj.category:
            return {'id': obj.category.pk, 'name': obj.category.name, 'slug': obj.category.slug}
        return None


class AccessoryStockSerializer(BaseModelSerializer):
    accessory = NestedAccessorySerializer(read_only=True)
    accessory_id = serializers.PrimaryKeyRelatedField(
        queryset=Accessory.objects.all(),
        source='accessory', write_only=True
    )
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='location', write_only=True
    )

    class Meta:
        model = AccessoryStock
        fields = [
            'id', 'accessory', 'accessory_id',
            'location', 'location_id',
            'qty', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'accessory', 'location', 'qty']


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
    from_location = NestedLocationSerializer(read_only=True)
    from_location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='from_location', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = AccessoryAssignment
        fields = [
            'id', 'accessory', 'accessory_id',
            'assigned_holder', 'assigned_holder_id',
            'assigned_location', 'assigned_location_id',
            'from_location', 'from_location_id',
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
    category = serializers.SerializerMethodField(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=_consumable_category_queryset(),
        source='category', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedTenantSerializer.Meta.model.objects.all(),
        source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    consumed_qty = serializers.IntegerField(read_only=True)
    available = serializers.IntegerField(read_only=True)

    class Meta:
        model = Consumable
        fields = [
            'id', 'name', 'slug', 'manufacturer', 'manufacturer_id',
            'category', 'category_id', 'part_number',
            'total_stock', 'consumed_qty', 'available',
            'min_qty', 'allow_overallocate', 'notes', 'tags',
            'tenant', 'tenant_id',
            'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'manufacturer', 'category', 'available']

    def get_category(self, obj):
        if obj.category:
            return {'id': obj.category.pk, 'name': obj.category.name, 'slug': obj.category.slug}
        return None


class ConsumableStockSerializer(BaseModelSerializer):
    consumable = NestedConsumableSerializer(read_only=True)
    consumable_id = serializers.PrimaryKeyRelatedField(
        queryset=Consumable.objects.all(),
        source='consumable', write_only=True
    )
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='location', write_only=True
    )

    class Meta:
        model = ConsumableStock
        fields = [
            'id', 'consumable', 'consumable_id',
            'location', 'location_id',
            'qty', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'consumable', 'location', 'qty']


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
    from_location = NestedLocationSerializer(read_only=True)
    from_location_id = serializers.PrimaryKeyRelatedField(
        queryset=NestedLocationSerializer.Meta.model.objects.all(),
        source='from_location', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = ConsumableAssignment
        fields = [
            'id', 'consumable', 'consumable_id',
            'assigned_holder', 'assigned_holder_id',
            'assigned_location', 'assigned_location_id',
            'from_location', 'from_location_id',
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
