"""Regression coverage for removing the assets GraphQL write surface."""

from django.test import SimpleTestCase

import assets.schema as assets_schema
from core.schema import schema


class AssetGraphQLMutationHelperRemovalTests(SimpleTestCase):
    def test_assets_schema_does_not_publish_a_mutation_type(self):
        self.assertFalse(hasattr(assets_schema, "Mutation"))

    def test_composed_schema_does_not_expose_asset_write_fields(self):
        if schema.mutation is None:
            return

        sdl = schema.as_str()
        for field_name in ("createAsset", "updateAsset", "deleteAsset"):
            with self.subTest(field=field_name):
                self.assertNotIn(f"{field_name}(", sdl)
