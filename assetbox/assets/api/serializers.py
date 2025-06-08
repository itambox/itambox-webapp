# assets/api/serializers.py
from rest_framework import serializers
from assets.models import Asset, AssetRole, Manufacturer, ActivityLog
from organization.models import Location
from organization.api.serializers import NestedLocationSerializer, NestedTenantSerializer # Import nested serializers from org

# Inspired by NetBox API serializers

#
# Nested Serializers
#

class NestedAssetRoleSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assetrole-detail')

    class Meta:
        model = AssetRole
        fields = ['id', 'url', 'name']

class NestedManufacturerSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:manufacturer-detail')

    class Meta:
        model = Manufacturer
        fields = ['id', 'url', 'name']

class NestedAssetSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:asset-detail')

    class Meta:
        model = Asset
        fields = ['id', 'url', 'name', 'asset_tag']

#
# Main Serializers
#

class AssetRoleSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assetrole-detail')
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssetRole
        fields = ['id', 'url', 'name', 'description', 'asset_count', 'created_at', 'updated_at']

class ManufacturerSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:manufacturer-detail')
    asset_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Manufacturer
        fields = ['id', 'url', 'name', 'description', 'asset_count', 'created_at', 'updated_at']

class AssetSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:asset-detail')
    asset_role = NestedAssetRoleSerializer(read_only=True)
    asset_role_id = serializers.PrimaryKeyRelatedField(
        queryset=AssetRole.objects.all(), source='asset_role', write_only=True, required=False, allow_null=True
    )
    manufacturer = NestedManufacturerSerializer(read_only=True)
    manufacturer_id = serializers.PrimaryKeyRelatedField(
        queryset=Manufacturer.objects.all(), source='manufacturer', write_only=True # Manufacturer is required
    )
    location = NestedLocationSerializer(read_only=True) # From organization app
    location_id = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), source='location', write_only=True, required=False, allow_null=True
    )
    # TODO: Add assigned_to (AssetHolderAssignment) representation?

    class Meta:
        model = Asset
        fields = [
            'id', 'url', 'name', 'asset_tag', 'serial_number', 'status',
            'asset_role', 'asset_role_id', 'manufacturer', 'manufacturer_id',
            'location', 'location_id', 'purchase_date', 'warranty_expiration',
            'notes', 'created_at', 'updated_at'
        ]

# TODO: ActivityLog serializer? Usually not needed via REST API, more for internal logging. 