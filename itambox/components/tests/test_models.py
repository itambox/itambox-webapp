from django.test import TestCase
from assets.models import Manufacturer, Category, AssetRole, Asset
from organization.models import Location, Site
from components.models import Component, ComponentStock, ComponentAllocation

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
        from django.db import IntegrityError
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
            asset=self.asset,
            qty_allocated=1,
        )
        self.assertIn('Core i9-13900K', str(alloc))
        self.assertIn('WS-001', str(alloc))

    def test_allocation_absolute_url(self):
        alloc = ComponentAllocation.objects.create(
            component=self.component, asset=self.asset, qty_allocated=1
        )
        url = alloc.get_absolute_url()
        self.assertIn(str(self.asset.pk), url)

    def test_allocation_default_qty(self):
        alloc = ComponentAllocation.objects.create(component=self.component, asset=self.asset)
        self.assertEqual(alloc.qty_allocated, 1)

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
            asset=self.asset,
            from_location=self.warehouse,
            qty_allocated=2
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
            asset=self.asset,
            from_location=self.warehouse,
            qty_allocated=2
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

        alloc.qty_allocated = 5
        alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 5)

        alloc.qty_allocated = 1
        alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)

    def test_allocation_update_location_reallocates_stock(self):
        desk_stock = ComponentStock.objects.create(component=self.component, location=self.desk, qty=5)

        alloc = ComponentAllocation.objects.create(
            component=self.component,
            asset=self.asset,
            from_location=self.warehouse,
            qty_allocated=3
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
            asset=self.asset,
            from_location=self.warehouse,
            qty_allocated=4
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)

        alloc.delete()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

        alloc.restore()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)
