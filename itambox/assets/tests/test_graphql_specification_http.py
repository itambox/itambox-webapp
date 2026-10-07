"""HTTP regression coverage for the remaining asset GraphQL query surface."""

import json

from django.test import TestCase

from assets.tests import test_graphql as graphql_fixtures


class GraphQLSpecificationHTTPTests(TestCase):
    def setUp(self):
        graphql_fixtures.GraphQLTestCase.setUp(self)

    def test_asset_specification_http_query_remains_available(self):
        query = f"{{ assets(requestedScope: {{ mode: TENANT, tenantId: {json.dumps(str(self.tenant_a.pk))} }}) {{ name }} }}"
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({"query": query}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token_a.key}",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("errors", payload)
        self.assertEqual([asset["name"] for asset in payload["data"]["assets"]], ["Laptop A"])
