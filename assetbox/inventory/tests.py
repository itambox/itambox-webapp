from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.exceptions import ValidationError
from assets.models import Manufacturer, AssetType, AssetRole, Category
from organization.models import Site, Location, AssetHolder
from licenses.models import License
from software.models import Software
from .models import Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, Kit, KitItem

User = get_user_model()


class AccessoryModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')

    def test_accessory_creation(self):
        acc = Accessory.objects.create(
            name='MX Keys Keyboard',
            manufacturer=self.manufacturer,
            category=Accessory.CATEGORY_KEYBOARD,
            qty=50,
            min_qty=5,
        )
        self.assertEqual(str(acc), 'Logitech MX Keys Keyboard')
        self.assertEqual(acc.remaining_qty, 50)
        self.assertEqual(acc.checked_out_qty, 0)

    def test_accessory_absolute_url(self):
        acc = Accessory.objects.create(name='Mouse', manufacturer=self.manufacturer)
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

    def test_accessory_remaining_qty_with_assignments(self):
        site = Site.objects.create(name='Office', slug='office')
        location = Location.objects.create(name='Desk 1', slug='desk-1', site=site)
        holder = AssetHolder.objects.create(first_name='Jane', last_name='Doe', upn='jane.doe')
        acc = Accessory.objects.create(name='Keyboard', manufacturer=self.manufacturer, qty=10)
        AccessoryAssignment.objects.create(accessory=acc, assigned_holder=holder, qty=3)
        self.assertEqual(acc.remaining_qty, 7)
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

    def test_assignment_single_target_constraint(self):
        acc = Accessory.objects.create(name='Cable', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            AccessoryAssignment.objects.create(
                accessory=acc, assigned_holder=self.holder,
                assigned_location=self.location, qty=1
            )


class ConsumableModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')

    def test_consumable_creation(self):
        con = Consumable.objects.create(
            name='LaserJet Toner',
            manufacturer=self.manufacturer,
            category=Consumable.CATEGORY_TONER,
            qty=100,
            min_qty=10,
        )
        self.assertEqual(str(con), 'HP LaserJet Toner')
        self.assertEqual(con.remaining_qty, 100)
        self.assertEqual(con.consumed_qty, 0)

    def test_consumable_absolute_url(self):
        con = Consumable.objects.create(name='Ink', manufacturer=self.manufacturer)
        url = con.get_absolute_url()
        self.assertIn(str(con.pk), url)

    def test_consumable_remaining_with_consumptions(self):
        site = Site.objects.create(name='Warehouse', slug='warehouse')
        location = Location.objects.create(name='Shelf A', slug='shelf-a', site=site)
        con = Consumable.objects.create(name='Paper', manufacturer=self.manufacturer, qty=500)
        ConsumableAssignment.objects.create(
            consumable=con, assigned_location=location, qty=200
        )
        self.assertEqual(con.remaining_qty, 300)
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

    def test_consumption_to_holder(self):
        con = Consumable.objects.create(name='Toner', manufacturer=self.manufacturer)
        assignment = ConsumableAssignment.objects.create(
            consumable=con, assigned_holder=self.holder, qty=5
        )
        self.assertEqual(assignment.qty, 5)
        self.assertIn('Alice Brown', str(assignment))

    def test_consumption_single_target_constraint(self):
        con = Consumable.objects.create(name='Ink', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ConsumableAssignment.objects.create(
                consumable=con, assigned_holder=self.holder,
                assigned_location=self.location, qty=1
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
        self.accessory = Accessory.objects.create(
            name='MX Master 3S', manufacturer=self.manufacturer, category=Accessory.CATEGORY_MOUSE, qty=20
        )

    def test_list_view(self):
        url = reverse('assets:accessory_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MX Master 3S')

    def test_detail_view(self):
        url = reverse('assets:accessory_detail', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MX Master 3S')

    def test_create_view_get(self):
        url = reverse('assets:accessory_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:accessory_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'K380 Keyboard',
            'slug': 'logitech-k380-keyboard',
            'category': Accessory.CATEGORY_KEYBOARD,
            'qty': 30,
            'min_qty': 3,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Accessory.objects.filter(name='K380 Keyboard').exists())

    def test_edit_view_get(self):
        url = reverse('assets:accessory_update', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:accessory_update', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'MX Master 3S Updated',
            'slug': 'logitech-mx-master-3s',
            'category': Accessory.CATEGORY_MOUSE,
            'qty': 25,
            'min_qty': 0,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.name, 'MX Master 3S Updated')

    def test_delete_view_get(self):
        url = reverse('assets:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post_no_assignments(self):
        url = reverse('assets:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Accessory.objects.filter(pk=self.accessory.pk).exists())

    def test_delete_view_blocked_with_assignments(self):
        site = Site.objects.create(name='Office', slug='office')
        location = Location.objects.create(name='Desk', slug='desk', site=site)
        holder = AssetHolder.objects.create(first_name='Test', last_name='User', upn='test.user')
        AccessoryAssignment.objects.create(accessory=self.accessory, assigned_holder=holder, qty=1)
        url = reverse('assets:accessory_delete', kwargs={'pk': self.accessory.pk})
        response = self.client.post(url)
        self.assertTrue(Accessory.objects.filter(pk=self.accessory.pk).exists())


class ConsumableViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.consumable = Consumable.objects.create(
            name='LaserJet Toner Cartridge',
            manufacturer=self.manufacturer,
            category=Consumable.CATEGORY_TONER,
            qty=50,
            min_qty=5,
        )

    def test_list_view(self):
        url = reverse('assets:consumable_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LaserJet Toner Cartridge')

    def test_detail_view(self):
        url = reverse('assets:consumable_detail', kwargs={'pk': self.consumable.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LaserJet Toner Cartridge')

    def test_create_view_get(self):
        url = reverse('assets:consumable_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:consumable_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'Ink Cartridge Black',
            'slug': 'hp-ink-cartridge-black',
            'category': Consumable.CATEGORY_INK,
            'qty': 200,
            'min_qty': 20,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Consumable.objects.filter(name='Ink Cartridge Black').exists())

    def test_edit_view_get(self):
        url = reverse('assets:consumable_update', kwargs={'pk': self.consumable.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:consumable_update', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': 'Updated Toner',
            'slug': 'hp-laserjet-toner-cartridge',
            'category': Consumable.CATEGORY_TONER,
            'qty': 100,
            'min_qty': 10,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.consumable.refresh_from_db()
        self.assertEqual(self.consumable.name, 'Updated Toner')
        self.assertEqual(self.consumable.qty, 100)

    def test_delete_view_post_no_consumptions(self):
        url = reverse('assets:consumable_delete', kwargs={'pk': self.consumable.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Consumable.objects.filter(pk=self.consumable.pk).exists())

    def test_delete_view_blocked_with_consumptions(self):
        site = Site.objects.create(name='Site', slug='site')
        location = Location.objects.create(name='Loc', slug='loc', site=site)
        ConsumableAssignment.objects.create(consumable=self.consumable, assigned_location=location, qty=1)
        url = reverse('assets:consumable_delete', kwargs={'pk': self.consumable.pk})
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
        url = reverse('assets:kit_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Standard Kit')

    def test_detail_view(self):
        url = reverse('assets:kit_detail', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Standard Kit')

    def test_create_view_get(self):
        url = reverse('assets:kit_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:kit_create')
        response = self.client.post(url, {
            'name': 'Developer Bundle',
            'description': 'Laptop + monitor',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Kit.objects.filter(name='Developer Bundle').exists())

    def test_edit_view_get(self):
        url = reverse('assets:kit_update', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:kit_update', kwargs={'pk': self.kit.pk})
        response = self.client.post(url, {
            'name': 'Updated Kit',
            'description': 'Renamed',
        })
        self.assertEqual(response.status_code, 302)
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.name, 'Updated Kit')

    def test_delete_view_get(self):
        url = reverse('assets:kit_delete', kwargs={'pk': self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('assets:kit_delete', kwargs={'pk': self.kit.pk})
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
        url = reverse('assets:kititem_create') + f'?kit={self.kit.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_kit_item_create_view_post(self):
        url = reverse('assets:kititem_create')
        response = self.client.post(url, {
            'kit': self.kit.pk,
            'asset_type': self.asset_type.pk,
            'qty': 1,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KitItem.objects.filter(kit=self.kit, asset_type=self.asset_type).exists())

    def test_kit_item_delete(self):
        item = KitItem.objects.create(kit=self.kit, asset_type=self.asset_type)
        url = reverse('assets:kititem_delete', kwargs={'pk': item.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KitItem.objects.filter(pk=item.pk).exists())
