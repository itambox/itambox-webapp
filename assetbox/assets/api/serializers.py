# assets/api/serializers.py
from rest_framework import serializers
from assets.models import Asset, AssetRole, Manufacturer, ActivityLog, AssetType, InstalledSoftware
from organization.models import Location, Tenant
from software.models import Software # Import Software model
# Correct imports for nested serializers
from organization.api.serializers import NestedLocationSerializer, NestedTenantSerializer
from extras.api.serializers import TagSerializer # Assuming this is defined in extras
from software.api.serializers import SoftwareSerializer # Assuming this is defined in software
from core.api.nested_serializers import (
    NestedAssetRoleSerializer,
    NestedManufacturerSerializer,
    NestedAssetSerializer,
    NestedAssetTypeSerializer
)

# Inspired by NetBox API serializers

#
# Remove Nested Serializer definitions from here
#

#
# Main Serializers
#

class AssetRoleSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assetrole-detail')
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetRole
        fields = ['id', 'name', 'slug', 'description', 'color', 'asset_count', 'created_at', 'updated_at']

class ManufacturerSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:manufacturer-detail')
    asset_count = serializers.IntegerField(read_only=True)
    # Add count for related software? Requires annotation in viewset
    # software_product_count = serializers.IntegerField(read_only=True) 

    class Meta:
        model = Manufacturer
        fields = ['id', 'name', 'slug', 'description', 'asset_count', 'created_at', 'updated_at']

class AssetTypeSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assettype-detail')
    manufacturer = NestedManufacturerSerializer(read_only=True)
    # Add count for related assets? Requires annotation in viewset
    # asset_count = serializers.IntegerField(read_only=True) 

    class Meta:
        model = AssetType
        fields = [
            'id', 'model', 'slug', 'manufacturer', 'part_number', 
            'cpu', 'ram_gb', 'storage_capacity_gb', 'storage_type', 'gpu',
            'description', 'comments', 'created_at', 'updated_at'
            # Add tags if needed: 'tags'
        ]

class AssetSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:asset-detail')
    asset_type = NestedAssetTypeSerializer(read_only=True)
    asset_type_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetType.objects.all(), source='asset_type', write_only=True
    )
    asset_role = NestedAssetRoleSerializer(read_only=True)
    assetrole_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetRole.objects.all(), source='asset_role', write_only=True, required=False, allow_null=True
    )
    location = NestedLocationSerializer(read_only=True) # From organization app
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location', write_only=True, required=False, allow_null=True
    )
    tenant = NestedTenantSerializer(read_only=True)
    tenant_id = serializers.PrimaryKeyRelatedField(
        queryset=Tenant.objects.all(), source='tenant', write_only=True, required=False, allow_null=True
    )
    # TODO: Add assigned_to (AssetHolderAssignment) representation?
    tags = TagSerializer(many=True, read_only=True) # Show assigned tags

    class Meta:
        model = Asset
        fields = [
            'id', 'name', 'asset_tag', 'serial_number', 'status',
            'asset_type', 'asset_type_id', 'asset_role', 'assetrole_id',
            'location', 'location_id', 'tenant', 'tenant_id',
            'purchase_date', 'warranty_expiration',
            'notes', 'tags', 'created_at', 'updated_at'
        ]

class InstalledSoftwareSerializer(serializers.ModelSerializer):
    """Serializer for the InstalledSoftware model."""
    # Use NestedAssetSerializer for the asset field
    asset = NestedAssetSerializer(read_only=True)
    asset_id = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.all(), source='asset', write_only=True
    )
    # Use nested SoftwareSerializer (if simple) or create NestedSoftwareSerializer
    # For now, keep software PK and add basic fields
    # software = SoftwareSerializer(read_only=True) 
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

# TODO: ActivityLog serializer? Usually not needed via REST API, more for internal logging. 