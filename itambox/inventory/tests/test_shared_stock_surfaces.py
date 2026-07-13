"""Grantee-facing surfaces for shared stock (ADR-0001 phase 4b).

A pool shared to the active tenant becomes VISIBLE (UI stock list, API list +
retrieve) but never mutable (API writes and the stock-adjust endpoint stay
owner-side); the recipient tenant sees inbound assignments and can run the
return workflow; the checkout form offers granted pools as sources.
"""
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer
from core.tests.mixins import TenantTestMixin
from inventory.forms import AccessoryCheckoutForm
from inventory.models import Accessory, AccessoryStock
from inventory.services import checkout_inventory_item
from organization.models import (
    AssetHolder, Location, Site, Tenant, TenantGroup, TenantResourceGrant,
)

User = get_user_model()


class SharedStockWorld(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_root = TenantGroup.objects.create(name='4b Root', slug='fourb-root')
        cls.group_child = TenantGroup.objects.create(
            name='4b Child', slug='fourb-child', parent=cls.group_root,
        )
        cls.owner = Tenant.objects.create(
            name='4b Owner', slug='fourb-owner', is_provider=True,
        )
        cls.grantee = Tenant.objects.create(
            name='4b Grantee', slug='fourb-grantee', managed_by=cls.owner,
            group=cls.group_child,
        )
        cls.third = Tenant.objects.create(name='4b Third', slug='fourb-third')

        owner_site = Site.objects.create(name='4b OSite', slug='fourb-osite', tenant=cls.owner)
        cls.owner_location = Location.objects.create(
            name='4b Depot', slug='fourb-depot', site=owner_site, tenant=cls.owner,
        )
        grantee_site = Site.objects.create(
            name='4b GSite', slug='fourb-gsite', tenant=cls.grantee,
        )
        cls.grantee_location = Location.objects.create(
            name='4b Office', slug='fourb-office', site=grantee_site, tenant=cls.grantee,
        )
        mfr = Manufacturer.objects.create(name='4b Mfg', slug='fourb-mfg')
        cls.accessory = Accessory.objects.create(
            name='4b Dock', slug='fourb-dock', manufacturer=mfr, tenant=cls.owner,
        )
        cls.stock = AccessoryStock.objects.create(
            accessory=cls.accessory, location=cls.owner_location, qty=10,
        )
        cls.holder = AssetHolder.objects.create(
            first_name='Vier', last_name='Bee', upn='vier.bee@4b', tenant=cls.grantee,
        )

    def _grant(self, access=TenantResourceGrant.ACCESS_USE, group=None):
        return TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant=None if group is not None else self.grantee,
            grantee_tenant_group=group,
            resource_type=ContentType.objects.get_for_model(AccessoryStock),
            resource_id=self.stock.pk,
            access_level=access,
        )


class SharedStockListVisibilityTests(SharedStockWorld):
    VIEW_PERMS = ['inventory.view_accessorystock']

    def test_grantee_sees_shared_pool_in_ui_list(self):
        self._grant()
        user = User.objects.create_user(username='fourb-viewer', password='x')
        self.client_login_to_tenant(user, self.grantee, role_permissions=self.VIEW_PERMS)
        response = self.client.get(reverse('inventory:accessorystock_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '4b Depot')

    def test_group_grant_covers_descendant_group_tenant_in_ui_list(self):
        self._grant(group=self.group_root)
        user = User.objects.create_user(username='fourb-viewer2', password='x')
        self.client_login_to_tenant(user, self.grantee, role_permissions=self.VIEW_PERMS)
        response = self.client.get(reverse('inventory:accessorystock_list'))
        self.assertContains(response, '4b Depot')

    def test_unrelated_tenant_does_not_see_pool(self):
        self._grant()
        user = User.objects.create_user(username='fourb-third', password='x')
        self.client_login_to_tenant(user, self.third, role_permissions=self.VIEW_PERMS)
        response = self.client.get(reverse('inventory:accessorystock_list'))
        self.assertNotContains(response, '4b Depot')

    def test_no_grant_no_visibility(self):
        user = User.objects.create_user(username='fourb-nogrant', password='x')
        self.client_login_to_tenant(user, self.grantee, role_permissions=self.VIEW_PERMS)
        response = self.client.get(reverse('inventory:accessorystock_list'))
        self.assertNotContains(response, '4b Depot')

    def test_revoked_grant_removes_visibility(self):
        grant = self._grant()
        grant.delete()
        user = User.objects.create_user(username='fourb-revoked', password='x')
        self.client_login_to_tenant(user, self.grantee, role_permissions=self.VIEW_PERMS)
        response = self.client.get(reverse('inventory:accessorystock_list'))
        self.assertNotContains(response, '4b Depot')


class SharedStockApiTests(SharedStockWorld):
    API_PERMS = [
        'inventory.view_accessorystock', 'inventory.change_accessorystock',
        'inventory.delete_accessorystock',
    ]

    def _api_login(self, tenant, username):
        user = User.objects.create_user(username=username, password='x')
        self.client_login_to_tenant(user, tenant, role_permissions=self.API_PERMS)
        return user

    def test_grantee_can_retrieve_shared_pool(self):
        self._grant()
        self._api_login(self.grantee, 'fourb-api1')
        url = reverse('api:inventory_api:accessorystock-detail', kwargs={'pk': self.stock.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['qty'], 10)

    def test_grantee_cannot_mutate_shared_pool(self):
        self._grant()
        self._api_login(self.grantee, 'fourb-api2')
        url = reverse('api:inventory_api:accessorystock-detail', kwargs={'pk': self.stock.pk})
        # The shared pool RESOLVES for the grantee (they legitimately know it
        # exists), so mutations are an explicit 403, not a concealing 404.
        self.assertEqual(
            self.client.patch(url, {'qty': 999}, content_type='application/json').status_code,
            403,
        )
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

    def test_unrelated_tenant_cannot_retrieve_pool(self):
        self._grant()
        self._api_login(self.third, 'fourb-api3')
        url = reverse('api:inventory_api:accessorystock-detail', kwargs={'pk': self.stock.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_grantee_sees_shared_pool_in_api_list(self):
        self._grant()
        self._api_login(self.grantee, 'fourb-api4')
        url = reverse('api:inventory_api:accessorystock-list')
        ids = [row['id'] for row in self.client.get(url).json()['results']]
        self.assertIn(self.stock.pk, ids)


class RecipientAssignmentTests(SharedStockWorld):
    def _checkout_to_grantee(self):
        self._grant()
        with self.tenant_context(self.grantee):
            return checkout_inventory_item(
                self.accessory, 2, holder=self.holder,
                source_location=self.owner_location,
            )

    def test_recipient_sees_inbound_assignment_in_list(self):
        assignment = self._checkout_to_grantee()
        user = User.objects.create_user(username='fourb-rec1', password='x')
        self.client_login_to_tenant(
            user, self.grantee, role_permissions=['inventory.view_accessoryassignment'],
        )
        response = self.client.get(reverse('inventory:accessoryassignment_list'))
        self.assertContains(response, 'Vier Bee')
        self.assertEqual(assignment.target_tenant_id, self.grantee.pk)

    def test_recipient_api_retrieve_is_read_only(self):
        assignment = self._checkout_to_grantee()
        user = User.objects.create_user(username='fourb-rec2', password='x')
        self.client_login_to_tenant(
            user, self.grantee,
            role_permissions=['inventory.view_accessoryassignment',
                              'inventory.change_accessoryassignment'],
        )
        url = reverse('api:inventory_api:accessoryassignment-detail', kwargs={'pk': assignment.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.patch(url, {'qty': 99}, content_type='application/json').status_code,
            403,
        )

    def test_recipient_can_check_in(self):
        assignment = self._checkout_to_grantee()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)
        user = User.objects.create_user(username='fourb-rec3', password='x')
        self.client_login_to_tenant(
            user, self.grantee, role_permissions=['inventory.change_accessory'],
        )
        response = self.client.post(
            reverse('inventory:accessory_checkin', kwargs={'pk': assignment.pk}),
        )
        self.assertIn(response.status_code, (200, 302))
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.deleted_at)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)  # returned to the OWNER's pool

    def test_unrelated_tenant_cannot_check_in(self):
        assignment = self._checkout_to_grantee()
        user = User.objects.create_user(username='fourb-rec4', password='x')
        self.client_login_to_tenant(
            user, self.third, role_permissions=['inventory.change_accessory'],
        )
        response = self.client.post(
            reverse('inventory:accessory_checkin', kwargs={'pk': assignment.pk}),
        )
        self.assertEqual(response.status_code, 404)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.deleted_at)


class StockAdjustBoundaryTests(SharedStockWorld):
    def test_owner_can_adjust_own_pool(self):
        user = User.objects.create_user(username='fourb-adj1', password='x')
        self.client_login_to_tenant(
            user, self.owner, role_permissions=['inventory.change_accessorystock'],
        )
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.stock.pk})
        response = self.client.post(url + '?action=increment')
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 11)

    def test_grantee_cannot_adjust_shared_pool(self):
        # ADR-0001: grantees may view/consume shared stock, never adjust it.
        self._grant()
        user = User.objects.create_user(username='fourb-adj2', password='x')
        self.client_login_to_tenant(
            user, self.grantee, role_permissions=['inventory.change_accessorystock'],
        )
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.stock.pk})
        response = self.client.post(url + '?action=increment')
        self.assertIn(response.status_code, (403, 404))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)


class CheckoutFormSourceTests(SharedStockWorld):
    def test_grantee_form_offers_granted_pool_location(self):
        self._grant()
        with self.tenant_context(self.grantee):
            form = AccessoryCheckoutForm(accessory=self.accessory)
            source_ids = set(
                form.fields['from_location'].queryset.values_list('pk', flat=True)
            )
            holder_ids = set(
                form.fields['assigned_holder'].queryset.values_list('pk', flat=True)
            )
        self.assertIn(self.owner_location.pk, source_ids)   # the granted pool
        self.assertIn(self.grantee_location.pk, source_ids)  # own locations
        self.assertEqual(holder_ids, {self.holder.pk})       # targets stay own-tenant

    def test_form_without_grant_offers_only_own_locations(self):
        with self.tenant_context(self.grantee):
            form = AccessoryCheckoutForm(accessory=self.accessory)
            source_ids = set(
                form.fields['from_location'].queryset.values_list('pk', flat=True)
            )
        self.assertNotIn(self.owner_location.pk, source_ids)
        self.assertIn(self.grantee_location.pk, source_ids)

    def test_owner_form_unchanged(self):
        owner_holder = AssetHolder.objects.create(
            first_name='Own', last_name='Er', upn='own.er@4b', tenant=self.owner,
        )
        with self.tenant_context(self.owner):
            form = AccessoryCheckoutForm(accessory=self.accessory)
            source_ids = set(
                form.fields['from_location'].queryset.values_list('pk', flat=True)
            )
            holder_ids = set(
                form.fields['assigned_holder'].queryset.values_list('pk', flat=True)
            )
        self.assertIn(self.owner_location.pk, source_ids)
        self.assertNotIn(self.grantee_location.pk, source_ids)
        self.assertEqual(holder_ids, {owner_holder.pk})
