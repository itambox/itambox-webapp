from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.db import transaction
from assets.models import Manufacturer, AssetType, AssetRole, Category, Asset
from organization.models import Site, Location, AssetHolder, Tenant
from licenses.models import License
from software.models import Software
from .models import Accessory, AccessoryStock, AccessoryAssignment, Consumable, ConsumableStock, ConsumableAssignment, Kit, KitItem

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
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse')
        self.location = Location.objects.create(name='Shelf A', slug='shelf-a', site=self.site)
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


class ConsumableModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse')
        self.location = Location.objects.create(name='Shelf B', slug='shelf-b', site=self.site)
        self.cat_toner = _create_category('Toner', consumable=True)
        self.cat_ink = _create_category('Ink', consumable=True)

    def _make_consumable(self, name, category, stock_qty=0):
        con = Consumable.objects.create(
            name=name,
            manufacturer=self.manufacturer,
            category=category,
        )
        if stock_qty:
            _add_stock(Consumable, ConsumableStock, con, self.location, stock_qty)
        return con

    def test_consumable_creation(self):
        con = self._make_consumable('LaserJet Toner', self.cat_toner, 100)
        con.min_qty = 10
        con.save()
        self.assertEqual(str(con), 'HP LaserJet Toner')
        self.assertEqual(con.available, 100)
        self.assertEqual(con.consumed_qty, 0)

    def test_consumable_absolute_url(self):
        con = Consumable.objects.create(name='Ink', manufacturer=self.manufacturer)
        url = con.get_absolute_url()
        self.assertIn(str(con.pk), url)

    def test_consumable_available_with_consumptions(self):
        con = self._make_consumable('Paper', self.cat_toner, 500)
        ConsumableAssignment.objects.create(
            consumable=con, assigned_location=self.location, qty=200
        )
        self.assertEqual(con.available, 300)
        self.assertEqual(con.consumed_qty, 200)

    def test_consumable_soft_delete(self):
        con = Consumable.objects.create(name='Batteries', manufacturer=self.manufacturer)
        con.delete()
        self.assertFalse(Consumable.objects.filter(pk=con.pk).exists())
        self.assertTrue(Consumable.all_objects.filter(pk=con.pk).exists())


class ConsumableAssignmentModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Brother', slug='brother')
        self.site = Site.objects.create(name='Site A', slug='site-a')
        self.location = Location.objects.create(name='Floor 1', slug='floor-1', site=self.site)
        self.holder = AssetHolder.objects.create(first_name='Alice', last_name='Brown', upn='alice.brown')
        self.asset_role = AssetRole.objects.create(name='Desktop', slug='desktop')
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model='OptiPlex 7010', slug='optiplex-7010')
        self.asset = Asset.objects.create(name='OptiPlex Desktop', asset_tag='OPTIPLEX-001', asset_type=self.asset_type, asset_role=self.asset_role)

    def test_consumption_to_holder(self):
        con = Consumable.objects.create(name='Toner', manufacturer=self.manufacturer)
        assignment = ConsumableAssignment.objects.create(
            consumable=con, assigned_holder=self.holder, qty=5
        )
        self.assertEqual(assignment.qty, 5)
        self.assertIn('Alice Brown', str(assignment))

    def test_consumption_to_asset(self):
        con = Consumable.objects.create(name='Thermal Paste', manufacturer=self.manufacturer)
        assignment = ConsumableAssignment.objects.create(
            consumable=con, assigned_asset=self.asset, qty=2
        )
        self.assertEqual(assignment.qty, 2)
        self.assertIn('OptiPlex Desktop', str(assignment))

    def test_consumption_single_target_constraint(self):
        con = Consumable.objects.create(name='Ink', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        
        # Holder + Location
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConsumableAssignment.objects.create(
                    consumable=con, assigned_holder=self.holder,
                    assigned_location=self.location, qty=1
                )
            
        # Holder + Asset
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConsumableAssignment.objects.create(
                    consumable=con, assigned_holder=self.holder,
                    assigned_asset=self.asset, qty=1
                )
            
        # Location + Asset
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConsumableAssignment.objects.create(
                    consumable=con, assigned_location=self.location,
                    assigned_asset=self.asset, qty=1
                )
            
        # All three
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ConsumableAssignment.objects.create(
                    consumable=con, assigned_holder=self.holder,
                    assigned_location=self.location, assigned_asset=self.asset, qty=1
                )


class KitModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Apple', slug='apple')

    def test_kit_creation(self):
        kit = Kit.objects.create(name='New Hire Kit', description='Standard equipment for new employees')
        self.assertEqual(str(kit), 'New Hire Kit')
        self.assertEqual(kit.description, 'Standard equipment for new employees')

    def test_kit_absolute_url(self):
        kit = Kit.objects.create(name='Developer Kit')
        url = kit.get_absolute_url()
        self.assertIn(str(kit.pk), url)

    def test_kit_name_unique(self):
        Kit.objects.create(name='Unique Kit')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Kit.objects.create(name='Unique Kit')


class KitItemModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.kit = Kit.objects.create(name='IT Starter Kit')
        self.software = Software.objects.create(name='Windows', manufacturer=self.manufacturer)

    def test_kit_item_asset_type(self):
        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model='Latitude 5550', slug='latitude-5550'
        )
        item = KitItem.objects.create(kit=self.kit, asset_type=asset_type)
        self.assertIn('Latitude 5550', str(item))

    def test_kit_item_accessory(self):
        acc = Accessory.objects.create(name='Dock', manufacturer=self.manufacturer)
        item = KitItem.objects.create(kit=self.kit, accessory=acc, qty=2)
        self.assertIn('Dock', str(item))

    def test_kit_item_license(self):
        lic = License.objects.create(name='M365 E5', software=self.software, seats=10)
        item = KitItem.objects.create(kit=self.kit, license=lic)
        self.assertIn('Windows', str(item))

    def test_kit_item_single_target_constraint(self):
        with self.assertRaises(ValidationError):
            KitItem.objects.create(kit=self.kit, asset_type=None, accessory=None, license=None)

    def test_kit_item_clean_no_target(self):
        item = KitItem(kit=self.kit)
        with self.assertRaises(ValidationError):
            item.clean()

    def test_kit_item_clean_multiple_targets(self):
        acc = Accessory.objects.create(name='Keyboard', manufacturer=self.manufacturer)
        lic = License.objects.create(name='Test License', software=self.software, seats=5)
        item = KitItem(kit=self.kit, accessory=acc, license=lic)
        with self.assertRaises(ValidationError):
            item.clean()


class AccessoryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Desk', slug='desk', site=self.site)
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


class ConsumableViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Shelf', slug='shelf', site=self.site)
        self.cat_toner = _create_category('Toner', consumable=True)
        self.cat_ink = _create_category('Ink', consumable=True)
        self.consumable = Consumable.objects.create(
            name='LaserJet Toner Cartridge',
            manufacturer=self.manufacturer,
            category=self.cat_toner,
        )
        ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=50)

    def test_list_view(self):
        url = reverse('inventory:consumable_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LaserJet Toner Cartridge')

    def test_detail_view(self):
        url = reverse('inventory:consumable_detail', kwargs={'pk': self.consumable.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LaserJet Toner Cartridge')

    def test_create_view_get(self):
        url = reverse('inventory:consumable_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('inventory:consumable_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'Ink Cartridge Black',
            'slug': 'hp-ink-cartridge-black',
            'category': self.cat_ink.pk,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Consumable.objects.filter(name='Ink Cartridge Black').exists())

    def test_edit_view_get(self):
        url = reverse('inventory:consumable_update', kwargs={'pk': self.consumable.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('inventory:consumable_update', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'Updated Toner',
            'slug': 'hp-laserjet-toner-cartridge',
            'category': self.cat_toner.pk,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.name, 'Updated Toner')

    def test_delete_view_post_no_consumptions(self):
        url = reverse('inventory:consumable_delete', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Consumable.objects.filter(pk=self.consumable.pk).exists())

    def test_delete_view_blocked_with_consumptions(self):
        ConsumableAssignment.objects.create(
            consumable=self.consumable, assigned_location=self.location, from_location=self.location, qty=1
        )
        url = reverse('inventory:consumable_delete', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url)
        self.assertTrue(Consumable.objects.filter(pk=self.consumable.pk).exists())


class KitViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.kit = Kit.objects.create(name='Standard Kit', description='Basic equipment')

    def test_list_view(self):
        url = reverse('inventory:kit_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Standard Kit')

    def test_detail_view(self):
        url = reverse('inventory:kit_detail', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Standard Kit')

    def test_create_view_get(self):
        url = reverse('inventory:kit_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('inventory:kit_create')
        response = self.client.post(url, {
            'name': 'Developer Bundle',
            'description': 'Laptop + monitor',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Kit.objects.filter(name='Developer Bundle').exists())

    def test_edit_view_get(self):
        url = reverse('inventory:kit_update', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('inventory:kit_update', kwargs={'pk': self.kit.pk})
        response = self.client.post(url, {
            'name': 'Updated Kit',
            'description': 'Renamed',
        })
        self.assertEqual(response.status_code, 302)
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.name, 'Updated Kit')

    def test_delete_view_get(self):
        url = reverse('inventory:kit_delete', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('inventory:kit_delete', kwargs={'pk': self.kit.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Kit.objects.filter(pk=self.kit.pk).exists())


class KitItemViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.kit = Kit.objects.create(name='Test Kit')
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model='XPS 15', slug='xps-15'
        )

    def test_kit_item_create_view_get(self):
        url = reverse('inventory:kititem_create') + f'?kit={self.kit.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_kit_item_create_view_post(self):
        url = reverse('inventory:kititem_create')
        response = self.client.post(url, {
            'kit': self.kit.pk,
            'asset_type': self.asset_type.pk,
            'qty': 1,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KitItem.objects.filter(kit=self.kit, asset_type=self.asset_type).exists())

    def test_kit_item_delete(self):
        item = KitItem.objects.create(kit=self.kit, asset_type=self.asset_type)
        url = reverse('inventory:kititem_delete', kwargs={'pk': item.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KitItem.objects.filter(pk=item.pk).exists())


class AccessoryStockFilterSetTests(TestCase):
    def setUp(self):
        from assets.models import Manufacturer
        from organization.models import Site, Location
        from inventory.models import Accessory, AccessoryStock
        
        self.manufacturer = Manufacturer.objects.create(name="Logitech", slug="logitech")
        self.site = Site.objects.create(name="Main HQ", slug="main-hq")
        self.loc1 = Location.objects.create(name="Server Room", slug="server-room", site=self.site)
        self.loc2 = Location.objects.create(name="Storage A", slug="storage-a", site=self.site)
        
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


class ConsumableStockFilterSetTests(TestCase):
    def setUp(self):
        from assets.models import Manufacturer
        from organization.models import Site, Location
        from inventory.models import Consumable, ConsumableStock
        
        self.manufacturer = Manufacturer.objects.create(name="Canon", slug="canon")
        self.site = Site.objects.create(name="Main HQ", slug="main-hq")
        self.loc1 = Location.objects.create(name="Server Room", slug="server-room", site=self.site)
        self.loc2 = Location.objects.create(name="Storage B", slug="storage-b", site=self.site)
        
        self.con1 = Consumable.objects.create(name="Toner Black", slug="toner-black", manufacturer=self.manufacturer)
        self.con2 = Consumable.objects.create(name="Toner Cyan", slug="toner-cyan", manufacturer=self.manufacturer)
        
        self.stock1 = ConsumableStock.objects.create(consumable=self.con1, location=self.loc1, qty=20)
        self.stock2 = ConsumableStock.objects.create(consumable=self.con2, location=self.loc2, qty=15)

    def test_filter_by_consumable(self):
        from inventory.filters import ConsumableStockFilterSet
        f = ConsumableStockFilterSet({'consumable': self.con1.pk}, queryset=ConsumableStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock1, f.qs)
        self.assertNotIn(self.stock2, f.qs)

    def test_filter_by_location(self):
        from inventory.filters import ConsumableStockFilterSet
        f = ConsumableStockFilterSet({'location': self.loc2.pk}, queryset=ConsumableStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock2, f.qs)
        self.assertNotIn(self.stock1, f.qs)

    def test_filter_search(self):
        from inventory.filters import ConsumableStockFilterSet
        f = ConsumableStockFilterSet({'q': 'Cyan'}, queryset=ConsumableStock.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.stock2, f.qs)
        self.assertNotIn(self.stock1, f.qs)


class KitConsumableFulfillmentTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse')
        self.location = Location.objects.create(name='Shelf A', slug='shelf-a', site=self.site)
        self.holder = AssetHolder.objects.create(first_name='John', last_name='Smith', upn='john.smith')
        self.cat_cable = _create_category('Cable', consumable=True)
        self.consumable = Consumable.objects.create(
            name='Cat6 Cable', manufacturer=self.manufacturer, category=self.cat_cable
        )
        self.stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=50)
        self.kit = Kit.objects.create(name='Developer Starter Kit')
        
    def test_kit_item_consumable_creation_and_fulfillment(self):
        # Create KitItem with consumable
        item = KitItem.objects.create(kit=self.kit, consumable=self.consumable, qty=3)
        self.assertEqual(str(item), '3x Consumable: Logitech Cat6 Cable')

        # Try setting both accessory and consumable; should raise validation error
        acc = Accessory.objects.create(name='Mouse', manufacturer=self.manufacturer)
        item.accessory = acc
        with self.assertRaises(ValidationError):
            item.clean()

        # Revert accessory to keep only consumable
        item.accessory = None
        item.clean()

        # Fulfill the kit using checkout_kit service
        from assets.services import checkout_kit
        checkout_kit(self.kit, holder=self.holder, source_location=self.location)

        # Verify consumable stock is decremented by 3
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 47)
        
        # Verify ConsumableAssignment exists
        self.assertTrue(ConsumableAssignment.objects.filter(
            consumable=self.consumable, assigned_holder=self.holder, from_location=self.location, qty=3
        ).exists())


class AccessoryCheckoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Desk', slug='desk', site=self.site)
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
        
        # Verify assignment created and stock decremented
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
            'qty': 15  # only 10 available
        })
        self.assertEqual(response.status_code, 200)
        # Should show validation error on form
        self.assertContains(response, 'currently in stock')
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=self.accessory).count(), 0)


class ConsumableCheckoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Shelf', slug='shelf', site=self.site)
        self.cat_toner = _create_category('Toner', consumable=True)
        self.consumable = Consumable.objects.create(
            name='LaserJet Cartridge', manufacturer=self.manufacturer, category=self.cat_toner
        )
        self.stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=5)
        self.holder = AssetHolder.objects.create(first_name='Bob', last_name='Jones', upn='bob.jones')
        
        self.asset_role = AssetRole.objects.create(name='Printer', slug='printer')
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model='LaserJet Pro', slug='laserjet-pro')
        self.asset = Asset.objects.create(name='HP Printer', asset_tag='HP-001', asset_type=self.asset_type, asset_role=self.asset_role)

    def test_checkout_view_get(self):
        url = reverse('inventory:consumable_checkout', kwargs={'pk': self.consumable.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_checkout_to_asset_success(self):
        url = reverse('inventory:consumable_checkout', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url, {
            'target_type': 'asset',
            'assigned_asset': self.asset.pk,
            'from_location': self.location.pk,
            'qty': 1,
            'notes': 'Test consumable checkout'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify assignment created and stock permanently decremented
        assignment = ConsumableAssignment.objects.get(consumable=self.consumable)
        self.assertEqual(assignment.assigned_asset, self.asset)
        self.assertEqual(assignment.qty, 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 4)

    def test_checkout_validation_insufficient_stock(self):
        url = reverse('inventory:consumable_checkout', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url, {
            'target_type': 'asset',
            'assigned_asset': self.asset.pk,
            'from_location': self.location.pk,
            'qty': 10  # only 5 available
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'currently in stock')


class InventoryFormValidationTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Apple', slug='apple')
        self.cat_accessory = _create_category('Mouse', accessory=True)
        self.cat_consumable = _create_category('Toner', consumable=True)
        self.kit = Kit.objects.create(name='New Employee Kit')

    def test_accessory_form_validation(self):
        from inventory.forms import AccessoryForm
        form = AccessoryForm(data={
            'manufacturer': self.manufacturer.pk,
            'name': 'Magic Mouse',
            'slug': 'apple-magic-mouse',
            'category': self.cat_accessory.pk,
        })
        self.assertTrue(form.is_valid())

        # Missing name
        form_invalid = AccessoryForm(data={
            'manufacturer': self.manufacturer.pk,
            'category': self.cat_accessory.pk,
        })
        self.assertFalse(form_invalid.is_valid())

    def test_consumable_form_validation(self):
        from inventory.forms import ConsumableForm
        form = ConsumableForm(data={
            'manufacturer': self.manufacturer.pk,
            'name': 'USB-C Cable',
            'slug': 'apple-usb-c-cable',
            'category': self.cat_consumable.pk,
        })
        self.assertTrue(form.is_valid())

    def test_kit_form_validation(self):
        from inventory.forms import KitForm
        form = KitForm(data={
            'name': 'Engineering Pack',
            'description': 'Laptops and docks',
        })
        self.assertTrue(form.is_valid())

    def test_kit_item_form_no_targets(self):
        from inventory.forms import KitItemForm
        form = KitItemForm(data={
            'kit': self.kit.pk,
            'qty': 1,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_kit_item_form_multiple_targets(self):
        from inventory.forms import KitItemForm
        acc = Accessory.objects.create(name='Key', manufacturer=self.manufacturer)
        con = Consumable.objects.create(name='Paper', manufacturer=self.manufacturer)
        form = KitItemForm(data={
            'kit': self.kit.pk,
            'accessory': acc.pk,
            'consumable': con.pk,
            'qty': 1,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)


class StockCRUDViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Site X', slug='site-x')
        self.location = Location.objects.create(name='Location Y', slug='location-y', site=self.site)
        
        self.accessory = Accessory.objects.create(name='Trackpad', manufacturer=self.manufacturer)
        self.consumable = Consumable.objects.create(name='Labels', manufacturer=self.manufacturer)
        
        self.acc_stock = AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=5)
        self.con_stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=10)

    def test_accessory_stock_views(self):
        # 1. List View
        url_list = reverse('inventory:accessorystock_list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 200)

        # 2. Edit View Get
        url_edit = reverse('inventory:accessorystock_update', kwargs={'pk': self.acc_stock.pk})
        response = self.client.get(url_edit)
        self.assertEqual(response.status_code, 200)

        # 3. Edit View Post
        response = self.client.post(url_edit, {
            'accessory': self.accessory.pk,
            'location': self.location.pk,
            'qty': 15,
        })
        self.assertEqual(response.status_code, 302)
        self.acc_stock.refresh_from_db()
        self.assertEqual(self.acc_stock.qty, 15)

        # 4. Delete View Post
        url_delete = reverse('inventory:accessorystock_delete', kwargs={'pk': self.acc_stock.pk})
        response = self.client.post(url_delete)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AccessoryStock.objects.filter(pk=self.acc_stock.pk).exists())

    def test_consumable_stock_views(self):
        # 1. List View
        url_list = reverse('inventory:consumablestock_list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 200)

        # 2. Edit View Post
        url_edit = reverse('inventory:consumablestock_update', kwargs={'pk': self.con_stock.pk})
        response = self.client.post(url_edit, {
            'consumable': self.consumable.pk,
            'location': self.location.pk,
            'qty': 25,
        })
        self.assertEqual(response.status_code, 302)
        self.con_stock.refresh_from_db()
        self.assertEqual(self.con_stock.qty, 25)

        # 3. Delete View Post
        url_delete = reverse('inventory:consumablestock_delete', kwargs={'pk': self.con_stock.pk})
        response = self.client.post(url_delete)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ConsumableStock.objects.filter(pk=self.con_stock.pk).exists())


class StockAlertsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Office', slug='office')
        self.location = Location.objects.create(name='Desk', slug='desk', site=self.site)
        self.cat_mouse = _create_category('Mouse', accessory=True)
        self.accessory = Accessory.objects.create(
            name='MX Mouse', manufacturer=self.manufacturer, category=self.cat_mouse, min_qty=5
        )
        self.stock = AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=4)
        
    def test_low_stock_threshold_alert(self):
        from core.models import AlertRule, AlertLog, NotificationChannel
        from core.tasks import evaluate_alert_rules_task
        from django.contrib.contenttypes.models import ContentType

        # Create low stock alert rule (threshold 3, but accessory min_qty is 5)
        rule = AlertRule.objects.create(
            name="Accessory Low Stock Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=3,
            severity=AlertRule.SEVERITY_WARNING,
            is_active=True
        )

        channel = NotificationChannel.objects.create(
            name="In-App Channel",
            channel_type='in_app',
            enabled=True
        )
        rule.channels.add(channel)

        # MX Mouse: stock is 4, safety threshold min_qty is 5 (so met)
        triggered = evaluate_alert_rules_task()
        self.assertGreaterEqual(triggered, 1)

        ct = ContentType.objects.get_for_model(self.accessory)
        alert_logs = AlertLog.objects.filter(rule=rule, content_type=ct, object_id=self.accessory.pk)
        self.assertEqual(alert_logs.count(), 1)
        
        alert = alert_logs.first()
        self.assertEqual(alert.status, AlertLog.STATUS_ACTIVE)
        self.assertIn("Low Stock: MX Mouse", alert.subject)


class InventoryTenantScopingTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Accessories
        self.acc_a = Accessory.objects.create(
            name="Accessory A", manufacturer=self.manufacturer, tenant=self.tenant_a
        )
        self.acc_b = Accessory.objects.create(
            name="Accessory B", manufacturer=self.manufacturer, tenant=self.tenant_b
        )
        self.acc_global = Accessory.objects.create(
            name="Accessory Global", manufacturer=self.manufacturer, tenant=None
        )

        # Consumables
        self.con_a = Consumable.objects.create(
            name="Consumable A", manufacturer=self.manufacturer, tenant=self.tenant_a
        )
        self.con_b = Consumable.objects.create(
            name="Consumable B", manufacturer=self.manufacturer, tenant=self.tenant_b
        )
        self.con_global = Consumable.objects.create(
            name="Consumable Global", manufacturer=self.manufacturer, tenant=None
        )

        # Kits
        self.kit_a = Kit.objects.create(
            name="Kit A", tenant=self.tenant_a
        )
        self.kit_b = Kit.objects.create(
            name="Kit B", tenant=self.tenant_b
        )
        self.kit_global = Kit.objects.create(
            name="Kit Global", tenant=None
        )

    def tearDown(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

    def test_tenant_a_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_a)

        # Accessories
        accs = list(Accessory.objects.all())
        self.assertIn(self.acc_a, accs)
        self.assertIn(self.acc_global, accs)
        self.assertNotIn(self.acc_b, accs)

        # Consumables
        cons = list(Consumable.objects.all())
        self.assertIn(self.con_a, cons)
        self.assertIn(self.con_global, cons)
        self.assertNotIn(self.con_b, cons)

        # Kits
        kits = list(Kit.objects.all())
        self.assertIn(self.kit_a, kits)
        self.assertIn(self.kit_global, kits)
        self.assertNotIn(self.kit_b, kits)

    def test_tenant_b_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_b)

        # Accessories
        accs = list(Accessory.objects.all())
        self.assertIn(self.acc_b, accs)
        self.assertIn(self.acc_global, accs)
        self.assertNotIn(self.acc_a, accs)

        # Consumables
        cons = list(Consumable.objects.all())
        self.assertIn(self.con_b, cons)
        self.assertIn(self.con_global, cons)
        self.assertNotIn(self.con_a, cons)

        # Kits
        kits = list(Kit.objects.all())
        self.assertIn(self.kit_b, kits)
        self.assertIn(self.kit_global, kits)
        self.assertNotIn(self.kit_a, kits)

    def test_no_tenant_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

        # Accessories
        accs = list(Accessory.objects.all())
        self.assertIn(self.acc_a, accs)
        self.assertIn(self.acc_b, accs)
        self.assertIn(self.acc_global, accs)

        # Consumables
        cons = list(Consumable.objects.all())
        self.assertIn(self.con_a, cons)
        self.assertIn(self.con_b, cons)
        self.assertIn(self.con_global, cons)

        # Kits
        kits = list(Kit.objects.all())
        self.assertIn(self.kit_a, kits)
        self.assertIn(self.kit_b, kits)
        self.assertIn(self.kit_global, kits)


class InventorySymmetryAndHTMXTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='SymmetryLogitech', slug='symmetrylogitech')
        self.site = Site.objects.create(name='OfficeSymmetry', slug='officesymmetry')
        self.location = Location.objects.create(name='DeskSymmetry', slug='desksymmetry', site=self.site)
        self.cat_mouse = _create_category('MouseSymmetry', accessory=True)
        self.cat_toner = _create_category('TonerSymmetry', consumable=True)
        
        self.accessory = Accessory.objects.create(
            name='MX Master 3S Symmetry', manufacturer=self.manufacturer, category=self.cat_mouse
        )
        self.acc_stock = AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=10)
        
        self.consumable = Consumable.objects.create(
            name='LaserJet Toner Cartridge Symmetry', manufacturer=self.manufacturer, category=self.cat_toner
        )
        self.con_stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=5)
        
        self.holder = AssetHolder.objects.create(first_name='AliceSym', last_name='SmithSym', upn='alicesym.smithsym')
        
        # Create assignments (this automatically deducts stock via model saves!)
        self.acc_assignment = AccessoryAssignment.objects.create(
            accessory=self.accessory, assigned_holder=self.holder, from_location=self.location, qty=2
        )
        self.con_assignment = ConsumableAssignment.objects.create(
            consumable=self.consumable, assigned_holder=self.holder, from_location=self.location, qty=1
        )

    def test_global_accessory_assignment_list_view(self):
        url = reverse('inventory:accessoryassignment_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MX Master 3S Symmetry')

    def test_global_consumable_consumption_list_view(self):
        url = reverse('inventory:consumableassignment_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LaserJet Toner Cartridge Symmetry')

    def test_accessory_stock_adjust_increment(self):
        # Initial 10 - 2 assigned = 8 remaining in database
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.acc_stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.acc_stock.refresh_from_db()
        self.assertEqual(self.acc_stock.qty, 9)
        self.assertContains(response, '9')

    def test_accessory_stock_adjust_decrement(self):
        # Initial 10 - 2 assigned = 8 remaining in database
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.acc_stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.acc_stock.refresh_from_db()
        self.assertEqual(self.acc_stock.qty, 7)
        self.assertContains(response, '7')

    def test_consumable_stock_adjust_increment(self):
        # Initial 5 - 1 assigned = 4 remaining in database
        url = reverse('inventory:consumablestock_adjust', kwargs={'pk': self.con_stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.con_stock.refresh_from_db()
        self.assertEqual(self.con_stock.qty, 5)
        self.assertContains(response, '5')

    def test_consumable_stock_adjust_decrement(self):
        # Initial 5 - 1 assigned = 4 remaining in database
        url = reverse('inventory:consumablestock_adjust', kwargs={'pk': self.con_stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.con_stock.refresh_from_db()
        self.assertEqual(self.con_stock.qty, 3)
        self.assertContains(response, '3')





