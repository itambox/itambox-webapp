from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Manufacturer, Category, AssetRole, Asset
from organization.models import Location, Site
from components.models import Component, ComponentStock, ComponentAllocation

User = get_user_model()

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
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_detail_view(self):
        url = reverse('components:component_detail', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_create_view_get(self):
        url = reverse('components:component_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('components:component_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '980 Pro 1TB',
            'slug': 'samsung-980-pro-1tb',
            'category': self.category.pk,
            'min_stock_level': 0,
            'specs': '{}',
            'tags': [],
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.assertTrue(Component.objects.filter(name='980 Pro 1TB').exists())

    def test_edit_view_get(self):
        url = reverse('components:component_update', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('components:component_update', kwargs={'pk': self.component.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '990 Pro 4TB',
            'slug': 'samsung-990-pro-4tb',
            'category': self.category.pk,
            'min_stock_level': 0,
            'specs': '{}',
            'tags': [],
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.component.refresh_from_db()
        self.assertEqual(self.component.name, '990 Pro 4TB')

    def test_delete_view(self):
        url = reverse('components:component_delete', kwargs={'pk': self.component.pk})
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
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=self.category
        )
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.asset = Asset.objects.create(name='SRV-001', asset_tag='SRV-001', asset_role=self.role)
        self.allocation = ComponentAllocation.objects.create(
            component=self.component,
            asset=self.asset,
            qty_allocated=2,
            notes='Initial setup',
        )

    def test_list_view(self):
        url = reverse('components:componentallocation_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_get(self):
        url = reverse('components:componentallocation_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('components:componentallocation_create')
        response = self.client.post(url, {
            'component': self.component.pk,
            'asset': self.asset.pk,
            'qty_allocated': 1,
        })
        self.assertEqual(response.status_code, 302)

    def test_edit_view_get(self):
        url = reverse('components:componentallocation_update', kwargs={'pk': self.allocation.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('components:componentallocation_update', kwargs={'pk': self.allocation.pk})
        response = self.client.post(url, {
            'component': self.component.pk,
            'asset': self.asset.pk,
            'qty_allocated': 3,
            'notes': 'Updated allocation',
        })
        self.assertEqual(response.status_code, 302)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.qty_allocated, 3)
        self.assertEqual(self.allocation.notes, 'Updated allocation')

    def test_delete_view(self):
        url = reverse('components:componentallocation_delete', kwargs={'pk': self.allocation.pk})
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
        url = reverse('components:componentstock_adjust', kwargs={'pk': self.stock.pk}) + '?action=increment'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 11)
        self.assertContains(response, '11')

    def test_component_stock_adjust_decrement(self):
        url = reverse('components:componentstock_adjust', kwargs={'pk': self.stock.pk}) + '?action=decrement'
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)
        self.assertContains(response, '9')
