from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from model_bakery import baker
from assets.models import Manufacturer
from ..models import Software

User = get_user_model()

class SoftwareViewTests(TestCase):
    def setUp(self):
        self.user = baker.make(User, is_staff=True, is_superuser=True)
        self.user.set_password('testpassword')
        self.user.save()
        self.client.login(username=self.user.username, password='testpassword')

        self.manufacturer = baker.make(Manufacturer, name='Microsoft', slug='microsoft')
        self.software = baker.make(
            Software,
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
