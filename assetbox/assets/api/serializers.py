from rest_framework import serializers
from core.api.base import BaseModelSerializer
from assetbox.api.fields import RelatedObjectCountField
from core.api.nested_serializers import (
    NestedAssetRoleSerializer,
    NestedManufacturerSerializer,
    NestedAssetSerializer,
    NestedAssetTypeSerializer
)
from assets.models import Asset, AssetRole, Manufacturer, ActivityLog, AssetType, InstalledSoftware
from organization.models import Location, Tenant
from software.models import Software
from organization.api.serializers import NestedLocationSerializer, NestedTenantSerializer
from extras.api.serializers import TagSerializer


class AssetRoleSerializer(BaseModelSerializer):
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetRole
        fields = ['id', 'name', 'slug', 'description', 'color', 'asset_count', 'created_at', 'updated_at']
        brief_fields = ['id', 'name', 'slug', 'color']


class ManufacturerSerializer(BaseModelSerializer):
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Manufacturer
        fields = ['id', 'name', 'slug', 'description', 'asset_count', 'created_at', 'updated_at']
        brief_fields = ['id', 'name', 'slug']


class AssetTypeSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)

    class Meta:
        model = AssetType
        fields = [
            'id', 'model', 'slug', 'manufacturer', 'part_number',
            'cpu', 'ram_gb', 'storage_capacity_gb', 'storage_type', 'gpu',
            'description', 'comments', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'model', 'slug', 'manufacturer']


class AssetSerializer(BaseModelSerializer):
    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetType.objects.all(), source='asset_type', write_only=True
    )
    asset_role = NestedAssetRoleSerializer(read_only=True)
    assetrole_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetRole.objects.all(), source='asset_role', write_only=True, required=False, allow_null=True
    )
    location = NestedLocationSerializer(read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'name', 'asset_tag', 'serial_number', 'status',
            'asset_type', 'asset_type_id', 'asset_role', 'assetrole_id',
            'location', 'location_id', 'tenant', 'tenant_id',
            'purchase_date', 'warranty_expiration',
            'notes', 'tags', 'created_at', 'updated_at'
        ]
        brief_fields = ['id', 'name', 'asset_tag', 'serial_number', 'status']


class InstalledSoftwareSerializer(BaseModelSerializer):
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )
    software_id = serializers.PrimaryKeyRelatedField(
        queryset=Software.objects.all(), source='software', write_only=True
    )
    software_name = serializers.CharField(source='software.name', read_only=True)
    software_manufacturer = serializers.CharField(source='software.manufacturer.name', read_only=True)

    class Meta:
        model = InstalledSoftware
        fields = (
            'id', 'asset', 'asset_id', 'software', 'software_id', 'software_name', 'software_manufacturer',
            'version_detected', 'install_date', 'discovered_by_agent',
            'last_seen_date', 'notes',
            'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')
        brief_fields = ['id', 'software_name', 'version_detected']
