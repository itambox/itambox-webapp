"""Regression coverage for removing the assets GraphQL write surface."""

from django.test import SimpleTestCase

import assets.schema as assets_schema
from core.schema import schema


class AssetGraphQLMutationHelperRemovalTests(SimpleTestCase):
    def test_assets_schema_does_not_publish_a_mutation_type(self):
        self.assertFalse(hasattr(assets_schema, "Mutation"))

    def test_composed_schema_does_not_expose_asset_write_fields(self):
        mutation_type = schema.graphql_schema.mutation_type
        if mutation_type is None:
            return

        for field_name in ("createAsset", "updateAsset", "deleteAsset"):
            with self.subTest(field=field_name):
                self.assertNotIn(field_name, mutation_type.fields)
