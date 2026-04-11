from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.exceptions import ValidationError
from assets.models import Manufacturer, AssetType, Category
from organization.models import Site, Location, AssetHolder
from licenses.models import License
from software.models import Software
from inventory.models import Accessory, Consumable, ConsumableStock, ConsumableAssignment, Kit, KitItem

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
        item = KitItem.objects.create(kit=self.kit, consumable=self.consumable, qty=3)
        self.assertEqual(str(item), '3x Consumable: Logitech Cat6 Cable')

        acc = Accessory.objects.create(name='Mouse', manufacturer=self.manufacturer)
        item.accessory = acc
        with self.assertRaises(ValidationError):
            item.clean()

        item.accessory = None
        item.clean()

        from assets.services import checkout_kit
        checkout_kit(self.kit, holder=self.holder, source_location=self.location)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 47)
        
        self.assertTrue(ConsumableAssignment.objects.filter(
            consumable=self.consumable, assigned_holder=self.holder, from_location=self.location, qty=3
        ).exists())
