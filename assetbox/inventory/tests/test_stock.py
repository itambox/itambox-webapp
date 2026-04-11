from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from assets.models import Manufacturer, Category
from organization.models import Site, Location
from inventory.models import Accessory, Consumable, AccessoryStock, ConsumableStock
from core.models import AlertRule, AlertLog, NotificationChannel

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
        from core.tasks import evaluate_alert_rules_task

        # Create low stock alert rule
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

        triggered = evaluate_alert_rules_task()
        self.assertGreaterEqual(triggered, 1)

        ct = ContentType.objects.get_for_model(self.accessory)
        alert_logs = AlertLog.objects.filter(rule=rule, content_type=ct, object_id=self.accessory.pk)
        self.assertEqual(alert_logs.count(), 1)
        
        alert = alert_logs.first()
        self.assertEqual(alert.status, AlertLog.STATUS_ACTIVE)
        self.assertIn("Low Stock: MX Mouse", alert.subject)
