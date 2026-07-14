from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import transaction
from assets.models import Manufacturer, AssetType, AssetRole, Category, Asset
from organization.models import Site, Location, AssetHolder, Tenant
from inventory.models import Accessory, AccessoryStock, AccessoryAssignment

User = get_user_model()

def _create_category(name, component=False, accessory=False, consumable=False):
    applies_to = {}
    if component:
        applies_to['component'] = True
    if accessory:
        applies_to['accessory'] = True
    if consumable:
        applies_to['consumable'] = True
    slug = name.lower().replace(' ', '-')
    cat, _ = Category.objects.get_or_create(
        slug=slug,
        defaults={'name': name, 'applies_to': applies_to}
    )
    return cat

def _add_stock(model_class, stock_model_class, catalog_item, location, qty):
    stock_model_class.objects.create(**{
        model_class._meta.model_name: catalog_item,
        'location': location,
        'qty': qty,
    })

class AccessoryModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Accessory Model", slug="tenant-accessory-model")
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse', tenant=self.tenant)
        self.location = Location.objects.create(name='Shelf A', slug='shelf-a', site=self.site, tenant=self.tenant)
        self.cat_keyboard = _create_category('Keyboard', accessory=True)
        self.cat_mouse = _create_category('Mouse', accessory=True)

    def _make_accessory(self, name, category, stock_qty=0):
        acc = Accessory.objects.create(
            name=name,
            manufacturer=self.manufacturer,
            category=category,
        )
        if stock_qty:
            _add_stock(Accessory, AccessoryStock, acc, self.location, stock_qty)
        return acc

    def test_accessory_creation(self):
        acc = self._make_accessory('MX Keys Keyboard', self.cat_keyboard, 50)
        acc.min_qty = 5
        acc.save()
        self.assertEqual(str(acc), 'Logitech MX Keys Keyboard')
        self.assertEqual(acc.available, 50)
        self.assertEqual(acc.checked_out_qty, 0)

    def test_accessory_absolute_url(self):
        acc = self._make_accessory('Mouse', self.cat_mouse)
        url = acc.get_absolute_url()
        self.assertIn(str(acc.pk), url)

    def test_accessory_slug_generation(self):
        acc = Accessory.objects.create(name='USB-C Hub', manufacturer=self.manufacturer)
        self.assertEqual(acc.slug, 'logitech-usb-c-hub')

    def test_accessory_unique_per_manufacturer(self):
        Accessory.objects.create(name='Test Item', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Accessory.objects.create(name='Test Item', manufacturer=self.manufacturer)

    def test_accessory_available_with_assignments(self):
        holder = AssetHolder.objects.create(first_name='Jane', last_name='Doe', upn='jane.doe')
        acc = Accessory.objects.create(name='Keyboard', manufacturer=self.manufacturer)
        AccessoryStock.objects.create(accessory=acc, location=self.location, qty=10)
        AccessoryAssignment.objects.create(accessory=acc, assigned_holder=holder, qty=3)
        self.assertEqual(acc.available, 7)
        self.assertEqual(acc.checked_out_qty, 3)

    def test_accessory_soft_delete(self):
        acc = Accessory.objects.create(name='Soft Delete Item', manufacturer=self.manufacturer)
        acc.delete()
        self.assertIsNotNone(acc.deleted_at)
        self.assertFalse(Accessory.objects.filter(pk=acc.pk).exists())
        self.assertTrue(Accessory.all_objects.filter(pk=acc.pk).exists())

class AccessoryAssignmentModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Room A', slug='room-a', site=self.site)
        self.holder = AssetHolder.objects.create(first_name='John', last_name='Smith', upn='john.smith')
        self.asset_role = AssetRole.objects.create(name='Laptop', slug='laptop')
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model='Latitude 5540', slug='latitude-5540')
        self.asset = Asset.objects.create(name='Dell Latitude', asset_tag='DELL-001', asset_type=self.asset_type, asset_role=self.asset_role)

    def test_assignment_to_holder(self):
        acc = Accessory.objects.create(name='Monitor', manufacturer=self.manufacturer)
        assignment = AccessoryAssignment.objects.create(
            accessory=acc, assigned_holder=self.holder, qty=2
        )
        self.assertEqual(assignment.qty, 2)
        self.assertIn('John Smith', str(assignment))

    def test_assignment_to_location(self):
        acc = Accessory.objects.create(name='Printer', manufacturer=self.manufacturer)
        assignment = AccessoryAssignment.objects.create(
            accessory=acc, assigned_location=self.location, qty=1
        )
        self.assertIn('Room A', str(assignment))

    def test_assignment_to_asset(self):
        acc = Accessory.objects.create(name='USB-C Mouse', manufacturer=self.manufacturer)
        assignment = AccessoryAssignment.objects.create(
            accessory=acc, assigned_asset=self.asset, qty=1
        )
        self.assertEqual(assignment.qty, 1)
        self.assertIn('Dell Latitude', str(assignment))

    def test_assignment_single_target_constraint(self):
        acc = Accessory.objects.create(name='Cable', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        
        # Holder + Location
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccessoryAssignment.objects.create(
                    accessory=acc, assigned_holder=self.holder,
                    assigned_location=self.location, qty=1
                )
            
        # Holder + Asset
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccessoryAssignment.objects.create(
                    accessory=acc, assigned_holder=self.holder,
                    assigned_asset=self.asset, qty=1
                )
            
        # Location + Asset
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccessoryAssignment.objects.create(
                    accessory=acc, assigned_location=self.location,
                    assigned_asset=self.asset, qty=1
                )
            
        # All three
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccessoryAssignment.objects.create(
                    accessory=acc, assigned_holder=self.holder,
                    assigned_location=self.location, assigned_asset=self.asset, qty=1
                )

class AccessoryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.tenant = Tenant.objects.create(name="Tenant Accessory View", slug="tenant-accessory-view")
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Office', slug='office', tenant=self.tenant)
        self.location = Location.objects.create(name='Desk', slug='desk', site=self.site, tenant=self.tenant)
        self.cat_keyboard = _create_category('Keyboard', accessory=True)
        self.cat_mouse = _create_category('Mouse', accessory=True)
        self.accessory = Accessory.objects.create(
            name='MX Master 3S', manufacturer=self.manufacturer, category=self.cat_mouse
        )
        AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=20)

    def test_list_view(self):
        url = reverse('inventory:accessory_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MX Master 3S')

        # Test unified inventory view for accessories redirect
        unified_url = reverse('inventory:inventory_list') + '?type=accessories'
        response = self.client.get(unified_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/inventory/accessories/', response.url)

    def test_detail_view(self):
        url = reverse('inventory:accessory_detail', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MX Master 3S')

    def test_create_view_get(self):
        url = reverse('inventory:accessory_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('inventory:accessory_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'K380 Keyboard',
            'slug': 'logitech-k380-keyboard',
            'category': self.cat_keyboard.pk,
            # A Tenant row exists in this TestCase (self.tenant, owning self.location's
            # stock), which makes core.apps's global form monkey-patch require 'tenant'
            # on any ModelForm carrying that field. Align this catalogue item into the
            # same tenant as the rest of the fixture.
            'tenant': self.tenant.pk,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Accessory.objects.filter(name='K380 Keyboard').exists())

    def test_edit_view_get(self):
        url = reverse('inventory:accessory_update', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('inventory:accessory_update', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'MX Master 3S Updated',
            'slug': 'logitech-mx-master-3s',
            'category': self.cat_mouse.pk,
            # See test_create_view_post: a Tenant exists in this TestCase, so the
            # form's 'tenant' field is required by core.apps's global monkey-patch.
            'tenant': self.tenant.pk,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.name, 'MX Master 3S Updated')

    def test_delete_view_get(self):
        url = reverse('inventory:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post_no_assignments(self):
        self.accessory.stocks.all().delete()
        url = reverse('inventory:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Accessory.objects.filter(pk=self.accessory.pk).exists())


    def test_delete_view_blocked_with_assignments(self):
        holder = AssetHolder.objects.create(first_name='Test', last_name='User', upn='test.user')
        AccessoryAssignment.objects.create(
            accessory=self.accessory, assigned_holder=holder, from_location=self.location, qty=1
        )
        url = reverse('inventory:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url)
        self.assertTrue(Accessory.objects.filter(pk=self.accessory.pk).exists())

class AccessoryCheckoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.tenant = Tenant.objects.create(name="Tenant Accessory Checkout", slug="tenant-accessory-checkout")
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Office', slug='office', tenant=self.tenant)
        self.location = Location.objects.create(name='Desk', slug='desk', site=self.site, tenant=self.tenant)
        self.cat_mouse = _create_category('Mouse', accessory=True)
        self.accessory = Accessory.objects.create(
            name='MX Master 3S', manufacturer=self.manufacturer, category=self.cat_mouse
        )
        self.stock = AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=10)
        self.holder = AssetHolder.objects.create(first_name='Alice', last_name='Smith', upn='alice.smith')
        
        self.asset_role = AssetRole.objects.create(name='Laptop', slug='laptop')
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model='XPS 15', slug='xps-15')
        self.asset = Asset.objects.create(name='Dell Laptop', asset_tag='DELL-002', asset_type=self.asset_type, asset_role=self.asset_role)

    def test_checkout_view_get(self):
        url = reverse('inventory:accessory_checkout', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_checkout_to_asset_success(self):
        url = reverse('inventory:accessory_checkout', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url, {
            'target_type': 'asset',
            'assigned_asset': self.asset.pk,
            'from_location': self.location.pk,
            'qty': 2,
            'notes': 'Test asset checkout'
        })
        self.assertEqual(response.status_code, 302)
        
        assignment = AccessoryAssignment.objects.get(accessory=self.accessory)
        self.assertEqual(assignment.assigned_asset, self.asset)
        self.assertEqual(assignment.qty, 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

    def test_checkout_to_holder_success(self):
        url = reverse('inventory:accessory_checkout', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url, {
            'target_type': 'holder',
            'assigned_holder': self.holder.pk,
            'from_location': self.location.pk,
            'qty': 1
        })
        self.assertEqual(response.status_code, 302)
        assignment = AccessoryAssignment.objects.get(accessory=self.accessory)
        self.assertEqual(assignment.assigned_holder, self.holder)
        self.assertEqual(assignment.qty, 1)

    def test_checkout_validation_insufficient_stock(self):
        url = reverse('inventory:accessory_checkout', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url, {
            'target_type': 'asset',
            'assigned_asset': self.asset.pk,
            'from_location': self.location.pk,
            'qty': 15
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'currently in stock')
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=self.accessory).count(), 0)

class AccessoryStockFilterSetTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Accessory Filter", slug="tenant-accessory-filter")
        self.manufacturer = Manufacturer.objects.create(name="Logitech", slug="logitech")
        self.site = Site.objects.create(name="Main HQ", slug="main-hq", tenant=self.tenant)
        self.loc1 = Location.objects.create(name="Server Room", slug="server-room", site=self.site, tenant=self.tenant)
        self.loc2 = Location.objects.create(name="Storage A", slug="storage-a", site=self.site, tenant=self.tenant)
        
        self.acc1 = Accessory.objects.create(name="Wired Mouse", slug="wired-mouse", manufacturer=self.manufacturer)
        self.acc2 = Accessory.objects.create(name="Wireless Keyboard", slug="wireless-keyboard", manufacturer=self.manufacturer)
        
        self.stock1 = AccessoryStock.objects.create(accessory=self.acc1, location=self.loc1, qty=10)
        self.stock2 = AccessoryStock.objects.create(accessory=self.acc2, location=self.loc2, qty=5)

    def test_filter_by_accessory(self):
        from inventory.filters import AccessoryStockFilterSet
        f = AccessoryStockFilterSet({'accessory': self.acc1.pk}, queryset=AccessoryStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock1, f.qs)
        self.assertNotIn(self.stock2, f.qs)

    def test_filter_by_location(self):
        from inventory.filters import AccessoryStockFilterSet
        f = AccessoryStockFilterSet({'location': self.loc2.pk}, queryset=AccessoryStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock2, f.qs)
        self.assertNotIn(self.stock1, f.qs)

    def test_filter_search(self):
        from inventory.filters import AccessoryStockFilterSet
        f = AccessoryStockFilterSet({'q': 'Wireless'}, queryset=AccessoryStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock2, f.qs)
        self.assertNotIn(self.stock1, f.qs)
