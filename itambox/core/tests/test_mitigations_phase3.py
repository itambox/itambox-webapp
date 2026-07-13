import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from model_bakery import baker

from organization.models import Tenant, TenantGroup, Membership, Role, Location, Site
from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer, Category, Supplier, Depreciation
from software.models import Software
from licenses.models import License
from users.models import Token
from core.managers import set_current_tenant_group, _descendant_group_ids_cache
from core.tests.mixins import grant

User = get_user_model()


class MitigationsPhase3Tests(TestCase):
    def setUp(self):
        # Setup users, tenants, and membership
        self.staff = User.objects.create_user(
            username='staff_user', email='staff@example.com', password='password123'
        )
        self.tenant_group = TenantGroup.objects.create(name="HQ Group", slug="hq-group")
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", group=self.tenant_group)
        self.site = Site.objects.create(name="Site", slug="site")

        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Staff Role',
            permissions=[
                'assets.view_asset',
                'software.view_software',
                'licenses.view_license',
            ]
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
            applies_to={"asset": True, "accessory": True, "component": True, "consumable": True}
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5540",
            slug="latitude-5540",
            category=self.category,
            asset_role=self.asset_role,
            depreciation=self.depreciation
        )
        self.location = Location.objects.create(name="Office", slug="office", tenant=self.tenant, site=self.site)
        self.supplier = Supplier.objects.create(name="Dell Supplier", slug="dell-supplier")
        
        # Create Assets
        self.asset = Asset.objects.create(
            name="Laptop", asset_tag="TAG-1", asset_type=self.asset_type,
            status=self.status, tenant=self.tenant, location=self.location,
            supplier=self.supplier
        )
        
        # Create Software & License
        self.software = Software.objects.create(name="Slack", manufacturer=self.manufacturer)
        self.license = License.objects.create(
            name="Slack License", software=self.software, tenant=self.tenant, seats=5,
            supplier=self.supplier
        )

        self.graphql_url = reverse('graphql')

    def tearDown(self):
        from core.managers import set_current_tenant, set_current_tenant_group, set_current_membership
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
        self.assertEqual(
            set(cache[root_group.pk]),
            {root_group.pk, child_group.pk, grandchild_group.pk}
        )
        
        # Subsequent evaluations should execute 0 tenantgroup queries
        with self.assertNumQueries(2):  # exactly 2 queries: 1 to assets, 1 to assets (again)
            list(Asset.objects.all())
            list(Asset.objects.all())

    def test_graphql_assets_select_related(self):
        # Request all relation fields: asset_type, asset_role, status, location, tenant, supplier
        query = '''
        {
          assets {
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
        '''
        
        # Create a second asset to ensure N+1 is not present
        Asset.objects.create(
            name="Laptop 2", asset_tag="TAG-2", asset_type=self.asset_type,
            status=self.status, tenant=self.tenant, location=self.location,
            supplier=self.supplier
        )
        
        # The key assertion is the single JOIN'd Asset query (select_related works — no
        # N+1 on asset relations). The remaining queries are auth/tenant/permission
        # overhead: token + last_used, tenant, TenantGroup, the membership lookup,
        # additive RoleGrant resolution (grant + prefetched scopes), a bounded
        # own-tenant coverage lookup, and session read/write. Managed projection is
        # skipped because this tenant is not managed by a provider.
        with self.assertNumQueries(19):
            response = self.client.post(
                self.graphql_url,
                data=json.dumps({'query': query}),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Token {self.token.key}'
            )
            self.assertEqual(response.status_code, 200)
            res_data = response.json()
            self.assertNotIn('errors', res_data)
            self.assertEqual(len(res_data['data']['assets']), 2)
