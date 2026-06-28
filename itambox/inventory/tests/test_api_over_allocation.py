"""WS2-1: the REST inventory-assignment CRUD path must enforce availability like
checkout_inventory_item(). Previously a POST with from_location omitted (or qty > available)
bypassed every check because adjust_inventory_stock only checks/deducts when from_location is set."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer, Category
from inventory.models import Accessory, AccessoryStock
from organization.models import (
    Tenant, Role, Membership, AssetHolder, Location, Site,
)

User = get_user_model()


class InventoryAssignmentOverAllocationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Tenant', slug='t-inv')
        self.user = User.objects.create_user(username='invuser', password='pw')
        role = Role.objects.create(
            tenant=self.tenant, name='Admin',
            permissions=['inventory.add_accessoryassignment', 'inventory.view_accessoryassignment'],
        )
        membership = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user, tenant=self.tenant)
        membership.roles.add(role)
        self.mfr = Manufacturer.objects.create(name='Logitech', slug='logi-inv')
        self.cat = Category.objects.create(
            name='Acc Cat', slug='acc-cat-inv', applies_to={'accessory': True}
        )
        self.accessory = Accessory.objects.create(
            name='Mouse', manufacturer=self.mfr, category=self.cat, tenant=self.tenant
        )
        self.site = Site.objects.create(name='Site', slug='site-inv')
        self.location = Location.objects.create(name='Loc', slug='loc-inv', site=self.site)
        # total_stock == 2 -> available == 2
        AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=2)
        self.holder = AssetHolder.objects.create(
            first_name='H', last_name='H', upn='h-inv', tenant=self.tenant
        )

    def _activate(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_cannot_over_allocate_without_from_location(self):
        self._activate()
        url = reverse('api:inventory_api:accessoryassignment-list')
        resp = self.client.post(url, {
            'accessory_id': self.accessory.pk,
            'assigned_holder_id': self.holder.pk,
            'qty': 5,  # > available (2), from_location omitted -> must be rejected
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_within_availability_succeeds(self):
        self._activate()
        url = reverse('api:inventory_api:accessoryassignment-list')
        resp = self.client.post(url, {
            'accessory_id': self.accessory.pk,
            'assigned_holder_id': self.holder.pk,
            'qty': 2,
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 201, resp.content)
