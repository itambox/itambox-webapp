import json
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from organization.models import Tenant, TenantRole, TenantMembership
from assets.models import Asset, StatusLabel, AssetRole, Manufacturer, AssetType

User = get_user_model()

class SecurityBoundariesTestCase(TestCase):
    def setUp(self):
        # Create Tenants
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Create Users
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        # Bind Tenant A User
        self.role_a = TenantRole.objects.create(
            tenant=self.tenant_a,
            name='Admin',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset'
            ]
        )
        self.membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
            role=self.role_a
        )

        # Bind Tenant B User
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset'
            ]
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b,
            tenant=self.tenant_b,
            role=self.role_b
        )

        # Create base metadata
        self.status = StatusLabel.objects.create(name='Active', slug='active')
        self.role = AssetRole.objects.create(name='Laptop', slug='laptop')
        self.mfr = Manufacturer.objects.create(name='Apple', slug='apple')
        self.asset_type = AssetType.objects.create(manufacturer=self.mfr, model='MacBook Pro')

        # Create Asset for Tenant B
        self.asset_b = Asset.objects.create(
            name='Asset of B',
            asset_tag='TAG-B-001',
            status=self.status,
            asset_role=self.role,
            asset_type=self.asset_type,
            tenant=self.tenant_b
        )

    def test_graphql_cross_tenant_query_denied(self):
        self.client.force_login(self.user_a)
        
        # Set request's active tenant to tenant_a
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Try querying Tenant B's asset via GraphQL
        query = f"""
        query {{
            asset(id: "{self.asset_b.pk}") {{
                id
                name
            }}
        }}
        """
        response = self.client.post(reverse('graphql'), data=json.dumps({'query': query}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data['data']['asset'])

    def test_graphql_cross_tenant_mutation_denied(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Try mutating Tenant B's asset
        query = f"""
        mutation {{
            updateAsset(id: "{self.asset_b.pk}", name: "Hacked Name") {{
                asset {{
                    id
                    name
                }}
            }}
        }}
        """
        response = self.client.post(reverse('graphql'), data=json.dumps({'query': query}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data.get('errors'))
        self.assertIn("Permission denied", data['errors'][0]['message'])

    def test_rest_api_cross_tenant_mutation_denied(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Try putting or deleting Tenant B's asset via REST API
        # First we check view detail is 404/denied
        detail_url = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_b.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 404)

        # Try changing it via API PUT
        put_data = {
            'name': 'Hacked Name',
            'asset_tag': 'TAG-B-001',
            'status': self.status.pk,
            'asset_role': self.role.pk,
            'asset_type': self.asset_type.pk,
            'tenant': self.tenant_b.pk
        }
        response = self.client.put(detail_url, data=put_data, content_type='application/json')
        self.assertEqual(response.status_code, 404)
