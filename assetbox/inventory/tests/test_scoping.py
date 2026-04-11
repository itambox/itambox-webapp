from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Manufacturer, Category
from organization.models import Location, AssetHolder, Tenant, Site
from inventory.models import Accessory, Consumable, AccessoryStock, ConsumableStock, AccessoryAssignment, ConsumableAssignment, Kit

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
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.acc_stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.acc_stock.refresh_from_db()
        self.assertEqual(self.acc_stock.qty, 9)
        self.assertContains(response, '9')

    def test_accessory_stock_adjust_decrement(self):
        url = reverse('inventory:accessorystock_adjust', kwargs={'pk': self.acc_stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.acc_stock.refresh_from_db()
        self.assertEqual(self.acc_stock.qty, 7)
        self.assertContains(response, '7')

    def test_consumable_stock_adjust_increment(self):
        url = reverse('inventory:consumablestock_adjust', kwargs={'pk': self.con_stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.con_stock.refresh_from_db()
        self.assertEqual(self.con_stock.qty, 5)
        self.assertContains(response, '5')

    def test_consumable_stock_adjust_decrement(self):
        url = reverse('inventory:consumablestock_adjust', kwargs={'pk': self.con_stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.con_stock.refresh_from_db()
        self.assertEqual(self.con_stock.qty, 3)
        self.assertContains(response, '3')
