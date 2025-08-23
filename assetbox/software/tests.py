from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Manufacturer
from .models import Software

User = get_user_model()


class SoftwareModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Microsoft', slug='microsoft')

    def test_software_creation(self):
        software = Software.objects.create(
            name='Windows 11 Enterprise',
            manufacturer=self.manufacturer,
            description='Operating system for enterprise',
        )
        self.assertEqual(str(software), 'Microsoft - Windows 11 Enterprise')
        self.assertEqual(software.manufacturer, self.manufacturer)

    def test_software_absolute_url(self):
        software = Software.objects.create(
            name='Office 365',
            manufacturer=self.manufacturer,
        )
        url = software.get_absolute_url()
        self.assertIn(str(software.pk), url)

    def test_software_ordering(self):
        Software.objects.create(name='B Software', manufacturer=self.manufacturer)
        Software.objects.create(name='A Software', manufacturer=self.manufacturer)
        qs = Software.objects.all()
        self.assertEqual(qs[0].name, 'A Software')
        self.assertEqual(qs[1].name, 'B Software')

    def test_software_name_unique(self):
        Software.objects.create(name='Unique Software', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Software.objects.create(name='Unique Software', manufacturer=self.manufacturer)


class SoftwareViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Microsoft', slug='microsoft')
        self.software = Software.objects.create(
            name='Visual Studio Code',
            manufacturer=self.manufacturer,
            description='Code editor',
        )

    def test_list_view(self):
        url = reverse('software:software_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visual Studio Code')

    def test_detail_view(self):
        url = reverse('software:software_detail', kwargs={'pk': self.software.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visual Studio Code')
        self.assertContains(response, 'Microsoft')

    def test_create_view_get(self):
        url = reverse('software:software_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('software:software_create')
        response = self.client.post(url, {
            'name': 'PowerShell 7',
            'manufacturer': self.manufacturer.pk,
            'description': 'Shell',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Software.objects.filter(name='PowerShell 7').exists())

    def test_edit_view_get(self):
        url = reverse('software:software_update', kwargs={'pk': self.software.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('software:software_update', kwargs={'pk': self.software.pk})
        response = self.client.post(url, {
            'name': 'VS Code Insiders',
            'manufacturer': self.manufacturer.pk,
            'description': 'Preview build',
        })
        self.assertEqual(response.status_code, 302)
        self.software.refresh_from_db()
        self.assertEqual(self.software.name, 'VS Code Insiders')

    def test_delete_view_get(self):
        url = reverse('software:software_delete', kwargs={'pk': self.software.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('software:software_delete', kwargs={'pk': self.software.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Software.objects.filter(pk=self.software.pk).exists())
