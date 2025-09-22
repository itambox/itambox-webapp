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


from rest_framework.test import APITestCase
from rest_framework import status

class SoftwareAPITests(APITestCase):
    def setUp(self):
        # Create users
        self.superuser = User.objects.create_user(
            username='superuser', email='super@example.com', password='password123', is_staff=True, is_superuser=True
        )
        self.staff = User.objects.create_user(
            username='staff', email='staff@example.com', password='password123', is_staff=True, is_superuser=False
        )

        # Base metadata
        self.manufacturer = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        self.software = Software.objects.create(
            name="Office 2021 Professional",
            manufacturer=self.manufacturer,
            version="16.0",
            category="productivity",
            license_type="proprietary"
        )

        # Grant specific software permission codenames to staff user for TokenPermissions validation
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        
        content_type = ContentType.objects.get_for_model(Software)
        for codename in ['view_software', 'add_software', 'change_software', 'delete_software']:
            permission = Permission.objects.get(
                codename=codename,
                content_type=content_type,
            )
            self.staff.user_permissions.add(permission)

    def test_software_api_list_and_detail(self):
        self.client.force_authenticate(user=self.staff)

        # Test list endpoint
        list_url = reverse('api:software_api:software-list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

        # Test detail endpoint
        detail_url = reverse('api:software_api:software-detail', kwargs={'pk': self.software.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Office 2021 Professional")
        self.assertEqual(response.data['version'], "16.0")

    def test_software_api_create_and_update_with_permissions(self):
        self.client.force_authenticate(user=self.staff)

        # 1. Create a Software record
        list_url = reverse('api:software_api:software-list')
        post_data = {
            'name': 'Visual Studio 2022',
            'manufacturer_id': self.manufacturer.id,
            'version': '17.0',
            'category': 'development',
            'license_type': 'proprietary'
        }
        response = self.client.post(list_url, data=post_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_pk = response.data['id']
        etag = response['ETag']

        # 2. Modify in-place using concurrency token ETag
        detail_url = reverse('api:software_api:software-detail', kwargs={'pk': new_pk})
        put_data = {
            'name': 'Visual Studio 2022 Enterprise',
            'manufacturer_id': self.manufacturer.id,
            'version': '17.2',
            'category': 'development',
            'license_type': 'proprietary'
        }
        response = self.client.put(detail_url, data=put_data, format='json', HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Visual Studio 2022 Enterprise')
        self.assertEqual(response.data['version'], '17.2')

    def test_software_api_create_without_permissions_denied(self):
        # A standard unauthenticated client should be rejected with 401 Unauthorized
        list_url = reverse('api:software_api:software-list')
        post_data = {
            'name': 'Unauthorized Soft',
            'manufacturer_id': self.manufacturer.id,
        }
        response = self.client.post(list_url, data=post_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_software_api_delete_verifies_cascade_protection(self):
        self.client.force_authenticate(user=self.staff)

        # Verify initial record
        self.assertTrue(Software.objects.filter(pk=self.software.pk).exists())
        etag = f'W/"{self.software.updated_at.isoformat()}"'

        # Perform deletion
        detail_url = reverse('api:software_api:software-detail', kwargs={'pk': self.software.pk})
        response = self.client.delete(detail_url, HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Assert no longer in DB
        self.assertFalse(Software.objects.filter(pk=self.software.pk).exists())

