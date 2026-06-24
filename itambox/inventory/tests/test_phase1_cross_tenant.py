from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from organization.models import Tenant, TenantRole, TenantMembership
from assets.models import Manufacturer, AssetType
from inventory.models import Kit, KitItem

User = get_user_model()


class KitItemCrossTenantTests(TestCase):
    """Phase 1 tenant-boundary coverage for KitItem (scoped via kit__tenant).

    KitItem has no direct tenant FK; it is scoped through its parent Kit.
    These tests target the REST API, which is where the TenantScopingManager
    + obj.tenant property + StrictTenantPermission boundary is enforced.
    """

    def setUp(self):
        # Two isolated tenants
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Users
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        # Roles grant read on KitItem (TokenPermissions checks view_kititem on GET)
        kititem_perms = ['inventory.view_kititem']

        self.role_a = TenantRole.objects.create(
            tenant=self.tenant_a, name='Admin', permissions=kititem_perms
        )
        self.membership_a = TenantMembership.objects.create(
            user=self.user_a, tenant=self.tenant_a
        )
        self.membership_a.roles.add(self.role_a)

        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b, name='Admin', permissions=kititem_perms
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b
        )
        self.membership_b.roles.add(self.role_b)

        # Shared metadata for kit-item targets
        self.mfr = Manufacturer.objects.create(name='Apple', slug='apple')
        self.asset_type = AssetType.objects.create(manufacturer=self.mfr, model='MacBook Pro')

        # A Kit + KitItem per tenant
        self.kit_a = Kit.objects.create(name='Kit A', tenant=self.tenant_a)
        self.kit_b = Kit.objects.create(name='Kit B', tenant=self.tenant_b)

        self.item_a = KitItem.objects.create(kit=self.kit_a, asset_type=self.asset_type, qty=1)
        self.item_b = KitItem.objects.create(kit=self.kit_b, asset_type=self.asset_type, qty=1)

    def _activate(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

    def test_list_returns_only_own_tenant_kit_items(self):
        self._activate(self.user_a, self.tenant_a)

        list_url = reverse('api:inventory_api:kititem-list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        results = data['results'] if isinstance(data, dict) and 'results' in data else data
        returned_ids = {row['id'] for row in results}

        self.assertIn(self.item_a.pk, returned_ids)
        self.assertNotIn(self.item_b.pk, returned_ids)

    def test_cross_tenant_detail_is_404(self):
        # Tenant A member tries to read Tenant B's kit item directly.
        self._activate(self.user_a, self.tenant_a)

        detail_url = reverse('api:inventory_api:kititem-detail', kwargs={'pk': self.item_b.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 404)

    def test_own_tenant_detail_is_visible(self):
        # Sanity: the boundary does not over-block; own-tenant detail is 200.
        self._activate(self.user_a, self.tenant_a)

        detail_url = reverse('api:inventory_api:kititem-detail', kwargs={'pk': self.item_a.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], self.item_a.pk)
