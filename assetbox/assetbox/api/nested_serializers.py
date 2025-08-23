from assetbox.api.base import BaseModelSerializer
from assets.models import Asset, AssetRole, Manufacturer, AssetType


class NestedAssetRoleSerializer(BaseModelSerializer):
    class Meta:
        model = AssetRole
        fields = ['id', 'name', 'color']
        brief_fields = ['id', 'name']


class NestedManufacturerSerializer(BaseModelSerializer):
    class Meta:
        model = Manufacturer
        fields = ['id', 'name']
        brief_fields = ['id', 'name']


class NestedAssetTypeSerializer(BaseModelSerializer):
    manufacturer = NestedManufacturerSerializer(read_only=True)

    class Meta:
        model = AssetType
        fields = ['id', 'model', 'manufacturer']
        brief_fields = ['id', 'model']


class NestedAssetSerializer(BaseModelSerializer):
    class Meta:
        model = Asset
        fields = ['id', 'name', 'asset_tag']
        brief_fields = ['id', 'name']
