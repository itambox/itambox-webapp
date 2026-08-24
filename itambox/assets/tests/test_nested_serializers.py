"""Contract tests for the asset-owned nested serializers."""

from django.test import SimpleTestCase

from assets.api.nested_serializers import (
    NestedAssetRoleSerializer,
    NestedAssetSerializer,
    NestedAssetTypeSerializer,
    NestedManufacturerSerializer,
)
from assets.models import Asset, AssetRole, AssetType, Manufacturer


class NestedAssetSerializerContractTests(SimpleTestCase):
    def test_serializer_classes_keep_their_asset_model_bindings_and_fields(self):
        self.assertIs(NestedAssetRoleSerializer.Meta.model, AssetRole)
        self.assertEqual(NestedAssetRoleSerializer.Meta.fields, ["id", "name", "color"])
        self.assertEqual(NestedAssetRoleSerializer.Meta.brief_fields, ["id", "name"])

        self.assertIs(NestedManufacturerSerializer.Meta.model, Manufacturer)
        self.assertEqual(NestedManufacturerSerializer.Meta.fields, ["id", "name"])
        self.assertEqual(NestedManufacturerSerializer.Meta.brief_fields, ["id", "name"])

        self.assertIs(NestedAssetTypeSerializer.Meta.model, AssetType)
        self.assertEqual(NestedAssetTypeSerializer.Meta.fields, ["id", "model", "manufacturer"])
        self.assertEqual(NestedAssetTypeSerializer.Meta.brief_fields, ["id", "model"])

        self.assertIs(NestedAssetSerializer.Meta.model, Asset)
        self.assertEqual(NestedAssetSerializer.Meta.fields, ["id", "name", "asset_tag"])
        self.assertEqual(NestedAssetSerializer.Meta.brief_fields, ["id", "name"])

    def test_nested_type_representation_keeps_manufacturer_shape(self):
        manufacturer = Manufacturer(name="Acme")
        asset_type = AssetType(model="Widget", manufacturer=manufacturer)

        self.assertEqual(
            NestedAssetTypeSerializer(asset_type).data,
            {"id": None, "model": "Widget", "manufacturer": {"id": None, "name": "Acme"}},
        )

    def test_asset_representation_keeps_brief_fields_and_asset_tag(self):
        asset = Asset(name="Asset one", asset_tag="TAG-1")

        self.assertEqual(
            NestedAssetSerializer(asset).data,
            {"id": None, "name": "Asset one", "asset_tag": "TAG-1"},
        )
