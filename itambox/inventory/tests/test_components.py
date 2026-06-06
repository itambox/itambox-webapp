from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import IntegrityError
from assets.models import Manufacturer, Category, AssetRole, Asset
from organization.models import Location, Site
from inventory.models import Component, ComponentStock, ComponentAllocation

User = get_user_model()

class ComponentModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.category = Category.objects.create(name='Storage', slug='storage', applies_to={'component': True})

    def test_component_creation(self):
        comp = Component.objects.create(
            name='990 Pro 2TB',
            manufacturer=self.manufacturer,
            category=self.category,
            part_number='MZ-V9P2T0B',
            specs={'capacity_gb': 2000, 'type': 'NVMe', 'interface': 'PCIe 4.0'},
        )
        self.assertEqual(str(comp), 'Samsung 990 Pro 2TB')
        self.assertEqual(comp.slug, 'samsung-990-pro-2tb')

    def test_component_absolute_url(self):
        comp = Component.objects.create(name='980 Pro', manufacturer=self.manufacturer, category=self.category)
        url = comp.get_absolute_url()
        self.assertIn(str(comp.pk), url)

    def test_component_unique_per_manufacturer(self):
        Component.objects.create(name='Test RAM', manufacturer=self.manufacturer, category=self.category)
        with self.assertRaises(IntegrityError):
            Component.objects.create(name='Test RAM', manufacturer=self.manufacturer, category=self.category)

    def test_component_stock_computation(self):
        comp = Component.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=self.category
        )
        site = Site.objects.create(name='Berlin HQ', slug='berlin-hq')
        location = Location.objects.create(name='Server Room A', slug='server-room-a', site=site)
        ComponentStock.objects.create(component=comp, location=location, qty=10)
        location2 = Location.objects.create(name='Server Room B', slug='server-room-b', site=site)
        ComponentStock.objects.create(component=comp, location=location2, qty=5)
        self.assertEqual(comp.total_stock, 15)
        self.assertEqual(comp.available_stock, 15)

class ComponentAllocationModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Intel', slug='intel')
        self.category = Category.objects.create(name='CPU', slug='cpu', applies_to={'component': True})
        self.component = Component.objects.create(
            name='Core i9-13900K', manufacturer=self.manufacturer, category=self.category
        )
        self.role = AssetRole.objects.create(name='Workstation', slug='workstation')
        self.asset = Asset.objects.create(name='WS-001', asset_tag='TAG-CPU-001', asset_role=self.role)

    def test_allocation_creation(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            qty=1,
        )
        self.assertIn('Core i9-13900K', str(alloc))
        self.assertIn('WS-001', str(alloc))

    def test_allocation_absolute_url(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component, assigned_asset=self.asset, qty=1
        )
        url = alloc.get_absolute_url()
        self.assertIn(str(self.asset.pk), url)

    def test_allocation_default_qty(self):
        alloc = ComponentAllocation.objects.create(component=self.component, assigned_asset=self.asset)
        self.assertEqual(alloc.qty, 1)

class ComponentWarehouseOriginTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.category = Category.objects.create(name='Storage', slug='storage', applies_to={'component': True})
        self.component = Component.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=self.category
        )
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.site = Site.objects.create(name='Munich HQ', slug='munich-hq')
        self.warehouse = Location.objects.create(name='Warehouse A', slug='warehouse-a', site=self.site)
        self.desk = Location.objects.create(name='Desk B', slug='desk-b', site=self.site)
        
        self.asset = Asset.objects.create(name='SRV-100', asset_tag='TAG-100', asset_role=self.role, location=self.desk)
        self.stock = ComponentStock.objects.create(component=self.component, location=self.warehouse, qty=10)

    def test_allocation_decrements_origin_stock(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=2
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

        self.asset.location = self.desk
        self.asset.save()

        alloc.delete()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)
        
        self.assertFalse(ComponentStock.objects.filter(component=self.component, location=self.desk).exists())

    def test_allocation_update_quantity_recalculates_stock(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=2
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

        alloc.qty = 5
        alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 5)

        alloc.qty = 1
        alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)

    def test_allocation_update_location_reallocates_stock(self):
        desk_stock = ComponentStock.objects.create(component=self.component, location=self.desk, qty=5)

        alloc = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=3
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 7)

        alloc.from_location = self.desk
        alloc.save()

        self.stock.refresh_from_db()
        desk_stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)
        self.assertEqual(desk_stock.qty, 2)

    def test_allocation_soft_delete_reverts_stock(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=4
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)

        alloc.delete()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

        alloc.restore()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)

class ComponentViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.category = Category.objects.create(name='Storage', slug='storage', applies_to={'component': True})
        self.component = Component.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=self.category
        )

    def test_list_view(self):
        url = reverse('components:component_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 301)
        self.assertIn('/inventory/inventory/?type=components', response.url)

        # Test the unified view for components redirect
        unified_url = reverse('inventory:inventory_list') + '?type=components'
        response = self.client.get(unified_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/inventory/components/', response.url)

        # Test the direct component list view
        direct_url = reverse('inventory:component_list')
        response = self.client.get(direct_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_detail_view(self):
        url = reverse('inventory:component_detail', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_create_view_get(self):
        url = reverse('inventory:component_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('inventory:component_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '980 Pro 1TB',
            'slug': 'samsung-980-pro-1tb',
            'category': self.category.pk,
            'min_qty': 0,
            'specs': '{}',
            'tags': [],
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.assertTrue(Component.objects.filter(name='980 Pro 1TB').exists())

    def test_edit_view_get(self):
        url = reverse('inventory:component_update', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('inventory:component_update', kwargs={'pk': self.component.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '990 Pro 4TB',
            'slug': 'samsung-990-pro-4tb',
            'category': self.category.pk,
            'min_qty': 0,
            'specs': '{}',
            'tags': [],
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.component.refresh_from_db()
        self.assertEqual(self.component.name, '990 Pro 4TB')

    def test_delete_view(self):
        url = reverse('inventory:component_delete', kwargs={'pk': self.component.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Component.objects.filter(pk=self.component.pk).exists())

class ComponentAllocationViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.category = Category.objects.create(name='Storage', slug='storage', applies_to={'component': True})
        self.component = Component.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=self.category,
            allow_overallocate=True
        )
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.asset = Asset.objects.create(name='SRV-001', asset_tag='SRV-001', asset_role=self.role)
        self.allocation = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            qty=2,
            notes='Initial setup',
        )

    def test_list_view(self):
        url = reverse('inventory:componentallocation_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_get(self):
        url = reverse('inventory:componentallocation_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('inventory:componentallocation_create')
        response = self.client.post(url, {
            'component': self.component.pk,
            'assigned_asset': self.asset.pk,
            'qty': 1,
        })
        self.assertEqual(response.status_code, 302)

    def test_edit_view_get(self):
        url = reverse('inventory:componentallocation_update', kwargs={'pk': self.allocation.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('inventory:componentallocation_update', kwargs={'pk': self.allocation.pk})
        response = self.client.post(url, {
            'component': self.component.pk,
            'assigned_asset': self.asset.pk,
            'qty': 3,
            'notes': 'Updated allocation',
        })
        self.assertEqual(response.status_code, 302)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.qty, 3)
        self.assertEqual(self.allocation.notes, 'Updated allocation')

    def test_delete_view(self):
        url = reverse('inventory:componentallocation_delete', kwargs={'pk': self.allocation.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ComponentAllocation.objects.filter(pk=self.allocation.pk).exists())

class ComponentStockAdjustViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='SamsungSym', slug='samsungsym')
        self.category = Category.objects.create(name='StorageSym', slug='storagesym', applies_to={'component': True})
        self.component = Component.objects.create(
            name='990 Pro 2TB Sym', manufacturer=self.manufacturer, category=self.category
        )
        self.site = Site.objects.create(name='OfficeSym', slug='officesym')
        self.location = Location.objects.create(name='DeskSym', slug='desksym', site=self.site)
        self.stock = ComponentStock.objects.create(component=self.component, location=self.location, qty=10)

    def test_component_stock_adjust_increment(self):
        url = reverse('inventory:componentstock_adjust', kwargs={'pk': self.stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 11)
        self.assertContains(response, '11')

    def test_component_stock_adjust_decrement(self):
        url = reverse('inventory:componentstock_adjust', kwargs={'pk': self.stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)
        self.assertContains(response, '9')
