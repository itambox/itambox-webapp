from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Manufacturer, AssetRole, Asset
from .models import ComponentType, ComponentInstance

User = get_user_model()


class ComponentTypeModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')

    def test_component_type_creation(self):
        ct = ComponentType.objects.create(
            name='990 Pro 2TB',
            manufacturer=self.manufacturer,
            category=ComponentType.CATEGORY_STORAGE,
            part_number='MZ-V9P2T0B',
            specs='2TB NVMe M.2 PCIe 4.0',
        )
        self.assertEqual(str(ct), 'Samsung 990 Pro 2TB')
        self.assertEqual(ct.slug, 'samsung-990-pro-2tb')

    def test_component_type_absolute_url(self):
        ct = ComponentType.objects.create(name='980 Pro', manufacturer=self.manufacturer)
        url = ct.get_absolute_url()
        self.assertIn(str(ct.pk), url)

    def test_component_type_unique_per_manufacturer(self):
        ComponentType.objects.create(name='Test RAM', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ComponentType.objects.create(name='Test RAM', manufacturer=self.manufacturer)

    def test_component_type_categories(self):
        categories = dict(ComponentType.CATEGORY_CHOICES)
        self.assertIn('ram', categories)
        self.assertIn('storage', categories)
        self.assertIn('gpu', categories)
        self.assertIn('cpu', categories)
        self.assertIn('nic', categories)
        self.assertIn('other', categories)

    def test_component_type_default_category(self):
        ct = ComponentType.objects.create(name='Default Cat', manufacturer=self.manufacturer)
        self.assertEqual(ct.category, ComponentType.CATEGORY_OTHER)


class ComponentInstanceModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Intel', slug='intel')
        self.component_type = ComponentType.objects.create(
            name='Core i9-13900K', manufacturer=self.manufacturer, category=ComponentType.CATEGORY_CPU
        )

    def test_component_instance_creation(self):
        ci = ComponentInstance.objects.create(
            component_type=self.component_type,
            serial_number='SN-CPU-001',
            status=ComponentInstance.STATUS_IN_STOCK,
        )
        self.assertIn('Intel Core i9-13900K', str(ci))
        self.assertIn('SN-CPU-001', str(ci))

    def test_component_instance_installed_in_asset(self):
        role = AssetRole.objects.create(name='Workstation', slug='workstation')
        asset = Asset.objects.create(name='WS-001', asset_tag='TAG-CPU-001', asset_role=role)
        ci = ComponentInstance.objects.create(
            component_type=self.component_type,
            serial_number='SN-CPU-002',
            parent_asset=asset,
            status=ComponentInstance.STATUS_INSTALLED,
        )
        self.assertEqual(ci.parent_asset, asset)
        self.assertIn('Intel Core i9-13900K', str(ci))

    def test_component_instance_absolute_url(self):
        ci = ComponentInstance.objects.create(
            component_type=self.component_type, status=ComponentInstance.STATUS_IN_STOCK
        )
        url = ci.get_absolute_url()
        self.assertIn(str(ci.pk), url)

    def test_component_instance_default_status(self):
        ci = ComponentInstance.objects.create(component_type=self.component_type)
        self.assertEqual(ci.status, ComponentInstance.STATUS_IN_STOCK)

    def test_component_instance_no_serial_number(self):
        ci = ComponentInstance.objects.create(
            component_type=self.component_type, status=ComponentInstance.STATUS_DEFECTIVE
        )
        self.assertNotIn('[S/N:', str(ci))


class ComponentTypeViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.component_type = ComponentType.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=ComponentType.CATEGORY_STORAGE
        )

    def test_list_view(self):
        url = reverse('assets:componenttype_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_detail_view(self):
        url = reverse('assets:componenttype_detail', kwargs={'pk': self.component_type.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '990 Pro 2TB')

    def test_create_view_get(self):
        url = reverse('assets:componenttype_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:componenttype_create')
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '980 Pro 1TB',
            'slug': 'samsung-980-pro-1tb',
            'category': ComponentType.CATEGORY_STORAGE,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.assertTrue(ComponentType.objects.filter(name='980 Pro 1TB').exists())

    def test_edit_view_get(self):
        url = reverse('assets:componenttype_update', kwargs={'pk': self.component_type.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:componenttype_update', kwargs={'pk': self.component_type.pk})
        response = self.client.post(url, {
            'manufacturer': self.manufacturer.pk,
            'name': '990 Pro 4TB',
            'slug': 'samsung-990-pro-4tb',
            'category': ComponentType.CATEGORY_STORAGE,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            self.fail(f'Form invalid. Errors: {form.errors if form else "no form in context"}')
        self.component_type.refresh_from_db()
        self.assertEqual(self.component_type.name, '990 Pro 4TB')

    def test_delete_view_get(self):
        url = reverse('assets:componenttype_delete', kwargs={'pk': self.component_type.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post_no_instances(self):
        url = reverse('assets:componenttype_delete', kwargs={'pk': self.component_type.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ComponentType.objects.filter(pk=self.component_type.pk).exists())

    def test_delete_view_blocked_with_instances(self):
        ComponentInstance.objects.create(
            component_type=self.component_type, status=ComponentInstance.STATUS_IN_STOCK
        )
        url = reverse('assets:componenttype_delete', kwargs={'pk': self.component_type.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ComponentType.objects.filter(pk=self.component_type.pk).exists())


class ComponentInstanceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.component_type = ComponentType.objects.create(
            name='990 Pro 2TB', manufacturer=self.manufacturer, category=ComponentType.CATEGORY_STORAGE
        )
        self.component = ComponentInstance.objects.create(
            component_type=self.component_type,
            serial_number='SN-001',
            status=ComponentInstance.STATUS_IN_STOCK,
        )

    def test_list_view(self):
        url = reverse('assets:componentinstance_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('assets:componentinstance_detail', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SN-001')

    def test_create_view_get(self):
        url = reverse('assets:componentinstance_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:componentinstance_create')
        response = self.client.post(url, {
            'component_type': self.component_type.pk,
            'serial_number': 'SN-002',
            'status': ComponentInstance.STATUS_INSTALLED,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ComponentInstance.objects.filter(serial_number='SN-002').exists())

    def test_edit_view_get(self):
        url = reverse('assets:componentinstance_update', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:componentinstance_update', kwargs={'pk': self.component.pk})
        response = self.client.post(url, {
            'component_type': self.component_type.pk,
            'serial_number': 'SN-001-UPDATED',
            'status': ComponentInstance.STATUS_DEFECTIVE,
            'notes': 'Replaced under warranty',
        })
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertEqual(self.component.serial_number, 'SN-001-UPDATED')
        self.assertEqual(self.component.status, ComponentInstance.STATUS_DEFECTIVE)
        self.assertEqual(self.component.notes, 'Replaced under warranty')

    def test_delete_view_get(self):
        url = reverse('assets:componentinstance_delete', kwargs={'pk': self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('assets:componentinstance_delete', kwargs={'pk': self.component.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ComponentInstance.objects.filter(pk=self.component.pk).exists())
