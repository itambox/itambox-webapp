from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Manufacturer, Category
from organization.models import Site, Location, AssetHolder, Tenant
from inventory.models import (
    Consumable, ConsumableStock, ConsumableAssignment,
    Accessory, AccessoryStock, AccessoryAssignment,
    Component, ComponentStock, ComponentAllocation
)

User = get_user_model()

class BulkCheckoutInventoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        # Give permissions
        self.client.login(username='testadmin', password='testpassword')
        
        self.tenant = Tenant.objects.create(name="Tenant Bulk Checkout", slug="tenant-bulk-checkout")
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse', tenant=self.tenant)
        self.loc_a = Location.objects.create(name='Shelf A', slug='shelf-a', site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name='Shelf B', slug='shelf-b', site=self.site, tenant=self.tenant)
        self.holder = AssetHolder.objects.create(first_name='John', last_name='Doe', upn='john.doe')
        
        self.cat_con = Category.objects.create(name='Consumable Cat', slug='con-cat', applies_to={'consumable': True})
        self.cat_acc = Category.objects.create(name='Accessory Cat', slug='acc-cat', applies_to={'accessory': True})
        self.cat_comp = Category.objects.create(name='Component Cat', slug='comp-cat', applies_to={'component': True})

        # Create items
        self.consumable1 = Consumable.objects.create(name='Toner 1', manufacturer=self.manufacturer, category=self.cat_con)
        self.consumable2 = Consumable.objects.create(name='Toner 2', manufacturer=self.manufacturer, category=self.cat_con)
        
        self.accessory1 = Accessory.objects.create(name='Mouse 1', manufacturer=self.manufacturer, category=self.cat_acc)
        self.accessory2 = Accessory.objects.create(name='Mouse 2', manufacturer=self.manufacturer, category=self.cat_acc)

        self.component1 = Component.objects.create(name='RAM 1', manufacturer=self.manufacturer, category=self.cat_comp)
        self.component2 = Component.objects.create(name='RAM 2', manufacturer=self.manufacturer, category=self.cat_comp)

        # Add stocks
        ConsumableStock.objects.create(consumable=self.consumable1, location=self.loc_a, qty=10)
        ConsumableStock.objects.create(consumable=self.consumable2, location=self.loc_a, qty=10)
        
        AccessoryStock.objects.create(accessory=self.accessory1, location=self.loc_a, qty=5)
        AccessoryStock.objects.create(accessory=self.accessory2, location=self.loc_b, qty=5)

        ComponentStock.objects.create(component=self.component1, location=self.loc_a, qty=8)
        ComponentStock.objects.create(component=self.component2, location=self.loc_b, qty=8)

        self.url = reverse('inventory:inventory_bulk_checkout')

    def test_bulk_checkout_consumables_catalog(self):
        # Checkout 2 consumables from catalog (requires source location)
        response = self.client.post(self.url, {
            'model_name': 'inventory.consumable',
            'pk': [self.consumable1.pk, self.consumable2.pk],
            'assigned_holder': self.holder.pk,
            'qty': 2,
            'from_location': self.loc_a.pk,
            'notes': 'Bulk catalog test'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify assignments are created
        self.assertEqual(ConsumableAssignment.objects.filter(assigned_holder=self.holder).count(), 2)
        # Verify stock is decremented
        self.assertEqual(ConsumableStock.objects.get(consumable=self.consumable1, location=self.loc_a).qty, 8)
        self.assertEqual(ConsumableStock.objects.get(consumable=self.consumable2, location=self.loc_a).qty, 8)

    def test_bulk_checkout_accessories_stocks(self):
        # Checkout from stocks page (resolves location per stock record)
        stock1 = AccessoryStock.objects.get(accessory=self.accessory1, location=self.loc_a)
        stock2 = AccessoryStock.objects.get(accessory=self.accessory2, location=self.loc_b)
        
        response = self.client.post(self.url, {
            'model_name': 'inventory.accessorystock',
            'pk': [stock1.pk, stock2.pk],
            'assigned_holder': self.holder.pk,
            'qty': 1,
            'notes': 'Bulk stock test'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify assignments
        self.assertEqual(AccessoryAssignment.objects.filter(assigned_holder=self.holder).count(), 2)
        # Verify stocks
        self.assertEqual(AccessoryStock.objects.get(pk=stock1.pk).qty, 4)
        self.assertEqual(AccessoryStock.objects.get(pk=stock2.pk).qty, 4)

    def test_bulk_checkout_components_stocks(self):
        stock1 = ComponentStock.objects.get(component=self.component1, location=self.loc_a)
        stock2 = ComponentStock.objects.get(component=self.component2, location=self.loc_b)
        
        response = self.client.post(self.url, {
            'model_name': 'inventory.componentstock',
            'pk': [stock1.pk, stock2.pk],
            'assigned_holder': self.holder.pk,
            'qty': 3,
            'notes': 'Bulk component stock test'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify allocations
        self.assertEqual(ComponentAllocation.objects.filter(assigned_holder=self.holder).count(), 2)
        self.assertEqual(ComponentStock.objects.get(pk=stock1.pk).qty, 5)
        self.assertEqual(ComponentStock.objects.get(pk=stock2.pk).qty, 5)
