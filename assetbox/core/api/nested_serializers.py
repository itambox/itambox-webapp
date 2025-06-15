from rest_framework import serializers

# Import models needed for nested serializers
from assets.models import Asset, AssetRole, Manufacturer, AssetType
# Import other models as needed (e.g., from organization, extras)

class NestedAssetRoleSerializer(serializers.ModelSerializer):
    # If using HyperlinkedIdentityField, ensure the view name is correct
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assetrole-detail')

    class Meta:
        model = AssetRole
        fields = ['id', 'name', 'color'] # Add color or other useful fields

class NestedManufacturerSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:manufacturer-detail')

    class Meta:
        model = Manufacturer
        fields = ['id', 'name']

class NestedAssetTypeSerializer(serializers.ModelSerializer):
     # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:assettype-detail')
     manufacturer = NestedManufacturerSerializer(read_only=True) # Nest manufacturer here

     class Meta:
         model = AssetType
         fields = ['id', 'model', 'manufacturer'] # Include nested manufacturer

class NestedAssetSerializer(serializers.ModelSerializer):
    # url = serializers.HyperlinkedIdentityField(view_name='api:assets_api:asset-detail')

    class Meta:
        model = Asset
        fields = ['id', 'name', 'asset_tag'] # Keep minimal

# Add other common nested serializers here (e.g., NestedSite, NestedLocation, NestedTag) as needed 