from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import transaction
from assets.models import Manufacturer, AssetType, AssetRole, Category, Asset
from organization.models import Site, Location, AssetHolder
from inventory.models import Consumable, ConsumableStock, ConsumableAssignment

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

        # Test unified inventory view for consumables redirect
        unified_url = reverse('inventory:inventory_list') + '?type=consumables'
        response = self.client.get(unified_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/inventory/consumables/', response.url)

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
            'qty': 10
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'currently in stock')

class ConsumableStockFilterSetTests(TestCase):
    def setUp(self):
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
