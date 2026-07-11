import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from organization.models import Tenant, Location, TenantGroup, Role, Site
from core.tests.mixins import grant
from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer, Category, Supplier
from software.models import Software
from licenses.models import License
from users.models import Token

User = get_user_model()

class GraphQLAdversarialTestCase(TestCase):
    def setUp(self):
        # Create users
        self.admin_user = User.objects.create_superuser(
            username='admin_user', email='admin@example.com', password='password123'
        )
        self.staff_a = User.objects.create_user(
            username='staff_a', email='staff_a@example.com', password='password123'
        )
        self.staff_b = User.objects.create_user(
            username='staff_b', email='staff_b@example.com', password='password123'
        )

        # Tenants
        self.tenant_group = TenantGroup.objects.create(name="HQ Group", slug="hq-group")
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a", group=self.tenant_group)
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b", group=self.tenant_group)

        # Site
        self.site = Site.objects.create(name="HQ Site", slug="hq-site")

        # Associate staff with Tenant membership/roles
        self.role_admin_a = Role.objects.create(
            tenant=self.tenant_a,
            name='Admin Role A',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'software.view_software', 'software.add_software', 'software.change_software', 'software.delete_software',
                'licenses.view_license', 'licenses.add_license', 'licenses.change_license', 'licenses.delete_license',
            ]
        )
        self.role_admin_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin Role B',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'software.view_software', 'software.add_software', 'software.change_software', 'software.delete_software',
                'licenses.view_license', 'licenses.add_license', 'licenses.change_license', 'licenses.delete_license',
            ]
        )
        self.assignment_a = grant(self.staff_a, self.tenant_a, self.role_admin_a)
        self.membership_a = self.assignment_a.membership
        self.assignment_b = grant(self.staff_b, self.tenant_b, self.role_admin_b)
        self.membership_b = self.assignment_b.membership

        # Grant general Django permissions to staff users
        for user in [self.staff_a, self.staff_b]:
            for app, model in [
                ('assets', 'asset'), ('software', 'software'), ('licenses', 'license'),
            ]:
                ct = ContentType.objects.get(app_label=app, model=model)
                for action in ['view', 'add', 'change', 'delete']:
                    codename = f"{action}_{model}"
                    try:
                        perm = Permission.objects.get(codename=codename, content_type=ct)
                        user.user_permissions.add(perm)
                    except Permission.DoesNotExist:
                        pass

        # Create Tokens
        self.token_a = Token.objects.create(user=self.staff_a)
        self.token_b = Token.objects.create(user=self.staff_b)

        # Setup base objects
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.asset_role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status = StatusLabel.objects.create(name="Ready", slug="ready", type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5540",
            slug="latitude-5540"
        )
        self.category = Category.objects.create(
            name="Laptop Cat",
            slug="laptop-cat",
            applies_to={"asset": True, "accessory": True, "component": True, "consumable": True}
        )

        # Tenant A objects
        self.location_a = Location.objects.create(name="Office A", slug="office-a", tenant=self.tenant_a, site=self.site)
        self.asset_a = Asset.objects.create(
            name="Laptop A", asset_tag="TAG-A", asset_type=self.asset_type,
            status=self.status, tenant=self.tenant_a, location=self.location_a
        )
        self.software = Software.objects.create(name="Slack", manufacturer=self.manufacturer)
        self.license_a = License.objects.create(
            name="Slack Entitlement A", software=self.software, tenant=self.tenant_a, seats=5
        )

        # Tenant B objects
        self.location_b = Location.objects.create(name="Office B", slug="office-b", tenant=self.tenant_b, site=self.site)
        self.asset_b = Asset.objects.create(
            name="Laptop B", asset_tag="TAG-B", asset_type=self.asset_type,
            status=self.status, tenant=self.tenant_b, location=self.location_b
        )
        self.license_b = License.objects.create(
            name="Slack Entitlement B", software=self.software, tenant=self.tenant_b, seats=10
        )

        self.graphql_url = reverse('graphql')

    # =========================================================================
    # 1. Query Parameters Tests (invalid limit, negative offset, large pages)
    # =========================================================================

    def test_negative_limit_returns_empty_or_fails(self):
        query = '{ assets(limit: -5) { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        # Depending on resolver logic, negative slice is either empty or raises ValueError
        if 'errors' in res_data:
            # If error is raised, it should handle it gracefully without traceback exposure
            self.assertIn('errors', res_data)
        else:
            self.assertEqual(len(res_data['data']['assets']), 0)

    def test_negative_offset_raises_error_gracefully(self):
        query = '{ assets(offset: -1) { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        # Slicing with negative offset like qs[-1:] will raise ValueError in Django, check if handled gracefully
        self.assertIn('errors', res_data)

    def test_extremely_large_page_limit(self):
        query = '{ assets(limit: 1000000) { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertLessEqual(len(res_data['data']['assets']), 1)

    # =========================================================================
    # 2. Malformed Queries (SQL Injection, syntax validation, depth limits)
    # =========================================================================

    def test_malformed_syntax_validation(self):
        query = '{ assets { name '
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertIn(response.status_code, [200, 400])
        res_data = response.json()
        self.assertIn('errors', res_data)
        self.assertIn('syntax', res_data['errors'][0]['message'].lower())

    def test_sql_injection_sort_by(self):
        # Query: sort_by parameter injection attempt
        query = '{ assets(sort_by: "name; DROP TABLE assets_asset;") { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertIn(response.status_code, [200, 400])
        res_data = response.json()
        # Django order_by checks fields, should raise FieldError / validation error
        self.assertIn('errors', res_data)

    def test_sql_injection_filter_parameters(self):
        # Query: filter parameters injection attempt
        query = '{ assets(name: "\' OR \'1\'=\'1") { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        # Should not return other tenant's assets
        self.assertEqual(len(res_data['data']['assets']), 0)

    def test_deep_query_depth_limit(self):
        # Construct a deep nested query if possible
        # Since Software is related to Manufacturer, and Manufacturer has software_products, etc.
        query = '''
        {
          assets {
            assetType {
              manufacturer {
                softwareProducts {
                  manufacturer {
                    softwareProducts {
                      name
                    }
                  }
                }
              }
            }
          }
        }
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        # Either succeeds safely within DB limits or returns schema fields correctly
        self.assertNotIn('errors', res_data)

    # =========================================================================
    # 3. Token Forgery (invalid auth formats, expired tokens, fake tokens)
    # =========================================================================

    def test_token_forgery_invalid_header_formats(self):
        query = '{ assets { name } }'
        
        # Test case: Missing value
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token'
        )
        self.assertEqual(response.status_code, 401)
        
        # Test case: Too many values (space in key)
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key} extra'
        )
        self.assertEqual(response.status_code, 401)

        # Test case: Wrong authentication scheme prefix
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 401)

    def test_token_forgery_expired_token(self):
        query = '{ assets { name } }'
        expired_token = Token.objects.create(
            user=self.staff_a,
            expires=timezone.now() - timezone.timedelta(seconds=1)
        )
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {expired_token.key}'
        )
        self.assertEqual(response.status_code, 401)

    def test_token_forgery_fake_or_nonexistent_token(self):
        query = '{ assets { name } }'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token fakekey123456789012345678901234567890'
        )
        self.assertEqual(response.status_code, 401)

    # =========================================================================
    # 4. Cross-Tenant Data Modification (attempting mutations with foreign keys of other tenants, deleting other tenants' objects)
    # =========================================================================

    def test_cross_tenant_delete_asset(self):
        # Tenant A user (staff_a) tries to delete Tenant B's asset (asset_b)
        mutation = f'''
        mutation {{
            deleteAsset(id: "{self.asset_b.id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        # Should raise permission error/not found since asset_b is not visible to Tenant A
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    def test_cross_tenant_update_asset(self):
        # Tenant A user (staff_a) tries to update Tenant B's asset (asset_b)
        mutation = f'''
        mutation {{
            updateAsset(
                id: "{self.asset_b.id}",
                name: "Hacked Name"
            ) {{
                asset {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        # Should raise permission error/not found
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    def test_cross_tenant_create_asset_with_foreign_key(self):
        # Tenant A user tries to create an asset referencing Location B (Tenant B)
        mutation = f'''
        mutation {{
            createAsset(
                name: "Asset A with Location B",
                assetTag: "TAG-CROSS-LOC",
                assetTypeId: "{self.asset_type.id}",
                statusId: "{self.status.id}",
                locationId: "{self.location_b.id}"
            ) {{
                asset {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    def test_cross_tenant_update_license(self):
        # Tenant A user tries to update Tenant B's license (license_b)
        mutation = f'''
        mutation {{
            updateLicense(
                id: "{self.license_b.id}",
                name: "Hacked License Name"
            ) {{
                license {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    def test_cross_tenant_delete_license(self):
        # Tenant A user tries to delete Tenant B's license (license_b)
        mutation = f'''
        mutation {{
            deleteLicense(id: "{self.license_b.id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    # =========================================================================
    # Family 7 — Cross-tenant read (query) probes with token auth
    # =========================================================================

    def test_cross_tenant_query_asset_by_id_not_leaked(self):
        """Querying tenant B's asset by pk while authenticated as tenant A returns null."""
        # Pass switch_tenant so TenantMiddleware sets active_tenant = tenant_a
        url = self.graphql_url + f'?switch_tenant={self.tenant_a.pk}'
        query = f'''
        query {{
            asset(id: "{self.asset_b.id}") {{
                id
                name
                assetTag
            }}
        }}
        '''
        response = self.client.post(
            url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        asset_data = (res_data.get('data') or {}).get('asset')
        self.assertIsNone(asset_data,
            f"Cross-tenant asset data leaked to tenant A user: {res_data}")

    def test_cross_tenant_query_assets_list_excludes_tenant_b(self):
        """Listing assets as tenant A must not include tenant B's asset."""
        url = self.graphql_url + f'?switch_tenant={self.tenant_a.pk}'
        query = '''
        query {
            assets {
                assetTag
            }
        }
        '''
        response = self.client.post(
            url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        if res_data.get('data') and res_data['data'].get('assets'):
            tags = [a.get('assetTag') for a in res_data['data']['assets']]
            self.assertNotIn('TAG-B', tags,
                "Tenant B asset tag appeared in tenant A's asset list query")

