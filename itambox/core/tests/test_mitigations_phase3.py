import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker

from assets.models import Asset, AssetRole, AssetType, Category, Depreciation, Manufacturer, StatusLabel, Supplier
from core.managers import _descendant_group_ids_cache, set_current_tenant_group
from core.tests.mixins import grant
from licenses.models import License
from organization.models import Location, Role, Site, Tenant, TenantGroup
from software.models import Software
from users.models import Token

User = get_user_model()


class MitigationsPhase3Tests(TestCase):
    def setUp(self):
        # Setup users, tenants, and membership
        self.staff = User.objects.create_user(username="staff_user", email="staff@example.com", password="password123")
        self.tenant_group = TenantGroup.objects.create(name="HQ Group", slug="hq-group")
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", group=self.tenant_group)
        self.site = Site.objects.create(name="Site", slug="site")

        self.role = Role.objects.create(
            tenant=self.tenant,
            name="Staff Role",
            permissions=[
                "assets.view_asset",
                "software.view_software",
                "licenses.view_license",
            ],
        )
        self.membership = grant(self.staff, self.tenant, self.role).membership
        self.token = Token.objects.create(user=self.staff)

        # Setup related objects to query in select_related
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.asset_role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status = StatusLabel.objects.create(name="Ready", slug="ready", type=StatusLabel.TYPE_DEPLOYABLE)
        self.depreciation = Depreciation.objects.create(name="Standard", months=36)

        self.category = Category.objects.create(
            name="Laptop Cat",
            slug="laptop-cat",
            applies_to={"asset": True, "accessory": True, "component": True, "consumable": True},
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5540",
            slug="latitude-5540",
            category=self.category,
            asset_role=self.asset_role,
            depreciation=self.depreciation,
        )
        self.location = Location.objects.create(name="Office", slug="office", tenant=self.tenant, site=self.site)
        self.supplier = Supplier.objects.create(name="Dell Supplier", slug="dell-supplier")

        # Create Assets
        self.asset = Asset.objects.create(
            name="Laptop",
            asset_tag="TAG-1",
            asset_type=self.asset_type,
            status=self.status,
            tenant=self.tenant,
            location=self.location,
            supplier=self.supplier,
        )

        # Create Software & License
        self.software = Software.objects.create(name="Slack", manufacturer=self.manufacturer)
        self.license = License.objects.create(
            name="Slack License", software=self.software, tenant=self.tenant, seats=5, supplier=self.supplier
        )

        self.graphql_url = reverse("graphql")

    def tearDown(self):
        from core.managers import set_current_membership, set_current_tenant, set_current_tenant_group

        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)

    def test_tenant_group_descendants_caching(self):
        root_group = baker.make(TenantGroup)
        child_group = baker.make(TenantGroup, parent=root_group)
        grandchild_group = baker.make(TenantGroup, parent=child_group)

        # Clear/initialize active group
        set_current_tenant_group(root_group)

        # Cache should be None initially
        self.assertIsNone(_descendant_group_ids_cache.get())

        # Trigger filter execution (runs DB query once for descendant list)
        list(Asset.objects.all())

        # Cache must contain group details now
        cache = _descendant_group_ids_cache.get()
        self.assertIsNotNone(cache)
        self.assertIn(root_group.pk, cache)
        self.assertEqual(set(cache[root_group.pk]), {root_group.pk, child_group.pk, grandchild_group.pk})

        # Subsequent evaluations should execute 0 tenantgroup queries
        with self.assertNumQueries(2):  # exactly 2 queries: 1 to assets, 1 to assets (again)
            list(Asset.objects.all())
            list(Asset.objects.all())

    def test_graphql_assets_select_related(self):
        # Request all relation fields: asset_type, asset_role, status, location, tenant, supplier
        query = """
        {
          assets(requestedScope: SCOPE_PLACEHOLDER) {
            name
            assetType {
              model
              manufacturer {
                name
              }
              category {
                name
              }
            }
            assetRole {
              name
            }
            status {
              name
            }
            location {
              name
            }
            tenant {
              name
            }
            supplier {
              name
            }
          }
        }
        """.replace("SCOPE_PLACEHOLDER", f'{{ mode: TENANT, tenantId: "{self.tenant.pk}" }}')

        def run_graphql_query():
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(
                    self.graphql_url,
                    data=json.dumps({"query": query}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION=f"Token {self.token.key}",
                )
            return response, tuple(entry["sql"] for entry in captured.captured_queries)

        # First establish the fixed authentication/scope/loader cost with one asset.
        first_response, first_queries = run_graphql_query()
        self.assertEqual(first_response.status_code, 200)
        first_data = first_response.json()
        self.assertNotIn("errors", first_data)
        self.assertEqual(len(first_data["data"]["assets"]), 1)

        # Expand only the asset fixture. A joined/prefetched read must not add one
        # query per returned row, while authorization remains a fixed request cost.
        Asset.objects.create(
            name="Laptop 2",
            asset_tag="TAG-2",
            asset_type=self.asset_type,
            status=self.status,
            tenant=self.tenant,
            location=self.location,
            supplier=self.supplier,
        )
        second_response, second_queries = run_graphql_query()
        self.assertEqual(second_response.status_code, 200)
        second_data = second_response.json()
        self.assertNotIn("errors", second_data)
        self.assertEqual(len(second_data["data"]["assets"]), 2)

        def asset_read_queries(queries):
            return [sql for sql in queries if 'from "assets_asset"' in sql.lower()]

        first_asset_queries = asset_read_queries(first_queries)
        second_asset_queries = asset_read_queries(second_queries)
        self.assertEqual(len(first_asset_queries), 1)
        self.assertEqual(len(second_asset_queries), 1)
        self.assertIn('inner join "assets_assettype"', second_asset_queries[0].lower())

        # Relation reads are constant as well: the expanded fixture must not cause
        # repeated prefetches, and total work may only grow by a bounded setup read.
        first_prefetch_queries = [sql for sql in first_queries if "assets_assettypefieldset" in sql.lower()]
        second_prefetch_queries = [sql for sql in second_queries if "assets_assettypefieldset" in sql.lower()]
        self.assertEqual(len(first_prefetch_queries), 1)
        self.assertEqual(len(second_prefetch_queries), 1)
        self.assertLessEqual(
            len(second_queries),
            len(first_queries) + 1,
            "GraphQL authorization/read query cost grew with fixture cardinality",
        )
