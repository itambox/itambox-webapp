import json
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from organization.models import Tenant, Location, TenantGroup, Membership, Role, Site
from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer, Category, Supplier
from software.models import Software
from licenses.models import License
from inventory.models import Accessory, Consumable, Kit, Component
from users.models import Token

User = get_user_model()

class GraphQLTestCase(TestCase):
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
                'inventory.view_component', 'inventory.add_component', 'inventory.change_component', 'inventory.delete_component',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory', 'inventory.delete_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable', 'inventory.delete_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit', 'inventory.delete_kit',
            ]
        )
        self.role_admin_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin Role B',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'software.view_software', 'software.add_software', 'software.change_software', 'software.delete_software',
                'licenses.view_license', 'licenses.add_license', 'licenses.change_license', 'licenses.delete_license',
                'inventory.view_component', 'inventory.add_component', 'inventory.change_component', 'inventory.delete_component',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory', 'inventory.delete_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable', 'inventory.delete_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit', 'inventory.delete_kit',
            ]
        )
        self.membership_a = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.staff_a, tenant=self.tenant_a)
        self.membership_a.roles.add(self.role_admin_a)
        self.membership_b = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.staff_b, tenant=self.tenant_b)
        self.membership_b.roles.add(self.role_admin_b)

        # Grant general Django permissions to staff users
        for user in [self.staff_a, self.staff_b]:
            for app, model in [
                ('assets', 'asset'), ('software', 'software'), ('licenses', 'license'),
                ('inventory', 'component'), ('inventory', 'accessory'), ('inventory', 'consumable'),
                ('inventory', 'kit')
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
        # Admin token: used in tests that require a superuser context so that
        # get_object_or_denied() runs without an active_tenant filter (superusers
        # have no forced tenant context when no session tenant is selected).
        self.token_admin = Token.objects.create(user=self.admin_user)

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
        # Tenant-owned so that get_object_or_denied(Software, ..., tenant=tenant_a)
        # can find it (the function adds an extra .filter(tenant=tenant) on top of
        # the TenantScopingSoftDeleteManager queryset, which would exclude a
        # global/null-tenant entry when active_tenant=tenant_a).
        self.software = Software.objects.create(name="Slack", manufacturer=self.manufacturer, tenant=self.tenant_a)
        self.license_a = License.objects.create(
            name="Slack Entitlement A", software=self.software, tenant=self.tenant_a, seats=5
        )
        self.component_a = Component.objects.create(
            name="RAM A", manufacturer=self.manufacturer, category=self.category, tenant=self.tenant_a
        )
        self.accessory_a = Accessory.objects.create(
            name="Mouse A", manufacturer=self.manufacturer, tenant=self.tenant_a
        )
        self.consumable_a = Consumable.objects.create(
            name="MX-4 Paste A", manufacturer=self.manufacturer, tenant=self.tenant_a
        )
        self.kit_a = Kit.objects.create(
            name="New Hire Kit A", tenant=self.tenant_a
        )

        # Tenant B objects
        self.location_b = Location.objects.create(name="Office B", slug="office-b", tenant=self.tenant_b, site=self.site)
        self.asset_b = Asset.objects.create(
            name="Laptop B", asset_tag="TAG-B", asset_type=self.asset_type,
            status=self.status, tenant=self.tenant_b, location=self.location_b
        )

        self.graphql_url = reverse('graphql')

    @override_settings(
        DEBUG=True,
        MIDDLEWARE=[m for m in settings.MIDDLEWARE if m != 'debug_toolbar.middleware.DebugToolbarMiddleware']
    )
    def test_graphiql_get_gated_by_session(self):
        # Unauthenticated GET request to GraphQL playground should redirect to login page
        response = self.client.get(self.graphql_url, HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

        # Authenticated GET request should load successfully (status 200)
        self.client.force_login(self.admin_user)
        response = self.client.get(self.graphql_url, HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 200)

    def test_graphql_post_gated_by_auth(self):
        query = '{ assets { name } }'
        # Unauthenticated POST request should return 401
        response = self.client.post(self.graphql_url, data={'query': query})
        self.assertEqual(response.status_code, 401)

        # Authenticated POST request with token should succeed
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)

    def test_tenant_isolation_boundary_query(self):
        # When querying under Tenant A, only Asset A should be returned
        query = '{ assets { name assetTag } }'
        
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        assets = res_data['data']['assets']
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['name'], 'Laptop A')

        # When querying under Tenant B, only Asset B should be returned
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_b.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        assets = res_data['data']['assets']
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['name'], 'Laptop B')

    def test_unauthorized_individual_lookup_returns_none(self):
        # Query asset of Tenant B as Staff A
        query = f'{{ asset(id: "{self.asset_b.id}") {{ name }} }}'
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertIsNone(res_data['data']['asset'])

    def test_mutations_create_and_validation(self):
        # Create asset in Tenant A
        mutation = f'''
        mutation {{
            createAsset(
                name: "New Asset A",
                assetTag: "TAG-NEW-A",
                assetTypeId: "{self.asset_type.id}",
                statusId: "{self.status.id}",
                locationId: "{self.location_a.id}"
            ) {{
                asset {{
                    name
                    tenant {{
                        slug
                    }}
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
        if response.status_code != 200:
            print("ERROR RESPONSE:", response.status_code, response.content)
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        asset_name = res_data['data']['createAsset']['asset']['name']
        self.assertEqual(asset_name, "New Asset A")
        tenant_slug = res_data['data']['createAsset']['asset']['tenant']['slug']
        self.assertEqual(tenant_slug, "tenant-a")

    def test_mutations_cross_tenant_foreign_key_fails(self):
        # Try to create asset in Tenant A using location_b (from Tenant B)
        mutation = f'''
        mutation {{
            createAsset(
                name: "Illegal Asset A",
                assetTag: "TAG-ILLEGAL-A",
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
        # Should raise permission error or validation error because location_b belongs to Tenant B
        self.assertIn('errors', res_data)
        self.assertIn('denied', res_data['errors'][0]['message'].lower())

    def test_post_request_using_session_auth(self):
        query = '{ assets { name } }'
        self.client.force_login(self.staff_a)
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': query}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(len(res_data['data']['assets']), 1)

    def test_crud_asset_update_delete(self):
        # Update Asset A
        mutation_update = f'''
        mutation {{
            updateAsset(
                id: "{self.asset_a.id}",
                name: "Updated Asset A"
            ) {{
                asset {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateAsset']['asset']['name'], "Updated Asset A")

        # Delete Asset A
        mutation_delete = f'''
        mutation {{
            deleteAsset(id: "{self.asset_a.id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteAsset']['success'])

    def test_crud_software(self):
        # Use the admin (superuser) token so that active_tenant=None on the
        # GraphQL context. createSoftware always creates global (tenant=None)
        # software entries; updateSoftware calls get_object_or_denied(...,
        # tenant=active_tenant) which adds an extra .filter(tenant=...) on the
        # queryset. When active_tenant is None the filter is skipped and the
        # global entry is found. A staff_a token would set active_tenant=tenant_a,
        # causing the filter to exclude the global entry and raise PermissionDenied.
        # Create
        mutation_create = f'''
        mutation {{
            createSoftware(
                name: "New Software",
                manufacturerId: "{self.manufacturer.id}",
                version: "1.0"
            ) {{
                software {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_admin.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        sw_id = res_data['data']['createSoftware']['software']['id']

        # Update
        mutation_update = f'''
        mutation {{
            updateSoftware(
                id: "{sw_id}",
                name: "Updated Software"
            ) {{
                software {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_admin.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateSoftware']['software']['name'], "Updated Software")

        # Delete
        mutation_delete = f'''
        mutation {{
            deleteSoftware(id: "{sw_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_admin.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteSoftware']['success'])

    def test_crud_license(self):
        # Create
        mutation_create = f'''
        mutation {{
            createLicense(
                name: "New License",
                softwareId: "{self.software.id}",
                seats: 10
            ) {{
                license {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        lic_id = res_data['data']['createLicense']['license']['id']

        # Update
        mutation_update = f'''
        mutation {{
            updateLicense(
                id: "{lic_id}",
                name: "Updated License"
            ) {{
                license {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateLicense']['license']['name'], "Updated License")

        # Delete
        mutation_delete = f'''
        mutation {{
            deleteLicense(id: "{lic_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteLicense']['success'])

    def test_crud_component(self):
        # Create
        mutation_create = f'''
        mutation {{
            createComponent(
                name: "New Component",
                manufacturerId: "{self.manufacturer.id}",
                categoryId: "{self.category.id}"
            ) {{
                component {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        comp_id = res_data['data']['createComponent']['component']['id']

        # Update
        mutation_update = f'''
        mutation {{
            updateComponent(
                id: "{comp_id}",
                name: "Updated Component"
            ) {{
                component {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateComponent']['component']['name'], "Updated Component")

        # Delete
        mutation_delete = f'''
        mutation {{
            deleteComponent(id: "{comp_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': mutation_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteComponent']['success'])

    def test_crud_inventory_items(self):
        # 1. Accessory CRUD
        acc_create = f'''
        mutation {{
            createAccessory(
                name: "New Acc",
                manufacturerId: "{self.manufacturer.id}"
            ) {{
                accessory {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': acc_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        acc_id = res_data['data']['createAccessory']['accessory']['id']

        acc_update = f'''
        mutation {{
            updateAccessory(
                id: "{acc_id}",
                name: "Updated Acc"
            ) {{
                accessory {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': acc_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateAccessory']['accessory']['name'], "Updated Acc")

        acc_delete = f'''
        mutation {{
            deleteAccessory(id: "{acc_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': acc_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteAccessory']['success'])

        # 2. Consumable CRUD
        cons_create = f'''
        mutation {{
            createConsumable(
                name: "New Cons",
                manufacturerId: "{self.manufacturer.id}"
            ) {{
                consumable {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': cons_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        cons_id = res_data['data']['createConsumable']['consumable']['id']

        cons_update = f'''
        mutation {{
            updateConsumable(
                id: "{cons_id}",
                name: "Updated Cons"
            ) {{
                consumable {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': cons_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateConsumable']['consumable']['name'], "Updated Cons")

        cons_delete = f'''
        mutation {{
            deleteConsumable(id: "{cons_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': cons_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteConsumable']['success'])

        # 3. Kit CRUD
        kit_create = f'''
        mutation {{
            createKit(
                name: "New Kit"
            ) {{
                kit {{
                    id
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': kit_create}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        kit_id = res_data['data']['createKit']['kit']['id']

        kit_update = f'''
        mutation {{
            updateKit(
                id: "{kit_id}",
                name: "Updated Kit"
            ) {{
                kit {{
                    name
                }}
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': kit_update}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertEqual(res_data['data']['updateKit']['kit']['name'], "Updated Kit")

        kit_delete = f'''
        mutation {{
            deleteKit(id: "{kit_id}") {{
                success
            }}
        }}
        '''
        response = self.client.post(
            self.graphql_url,
            data=json.dumps({'query': kit_delete}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token_a.key}'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertNotIn('errors', res_data)
        self.assertTrue(res_data['data']['deleteKit']['success'])

