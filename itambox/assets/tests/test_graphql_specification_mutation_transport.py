"""Regression coverage for the asset-specification GraphQL read transport."""

from django.test import SimpleTestCase

from core.schema import schema


class AssetSpecificationGraphQLTransportTests(SimpleTestCase):
    def test_query_operation_remains_available(self):
        result = schema.execute_sync("{ __typename }")

        self.assertIsNone(result.errors)
        self.assertEqual(result.data, {"__typename": "Query"})
