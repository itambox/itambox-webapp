from django.test import TestCase
from django.urls import reverse
from model_bakery import baker
from software.models import Software
from django.contrib.auth import get_user_model
from ..models import License, LicenseTypeChoices

User = get_user_model()

class LicenseViewTests(TestCase):
    def setUp(self):
        self.user = baker.make(User, is_staff=True, is_superuser=True)
        self.user.set_password('testpassword')
        self.user.save()
        self.client.login(username=self.user.username, password='testpassword')

        # Create Software via model-bakery with custom nested manufacturer
        self.software = baker.make(
            Software,
            name="Office 365 Enterprise",
            manufacturer__name="Microsoft",
            manufacturer__slug="microsoft",
            description="Office suite for enterprise"
        )

        # Create a test license
        self.license = baker.make(
            License,
            name="Office 365 E5 Renewal FY26",
            software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            seats=50,
            product_key="XXXX-XXXX-XXXX-XXXX",
            tenant=None
        )

    def test_license_list_view(self):
        """Verify that the License List view loads successfully and renders the test license."""
        url = reverse('licenses:license_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.license.name)
        self.assertTemplateUsed(response, 'generic/object_list.html')

    def test_license_detail_view(self):
        """Verify that the License Detail view resolves and renders the seat tracking layout."""
        url = reverse('licenses:license_detail', kwargs={'pk': self.license.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.license.name)
        self.assertContains(response, "Seat Assignments")
        self.assertTemplateUsed(response, 'licenses/license_detail.html')

    def test_license_create_view_loads(self):
        """Verify that the License Add form view loads successfully."""
        url = reverse('licenses:license_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'generic/object_edit.html')

    def test_license_edit_view_post(self):
        url = reverse('licenses:license_update', kwargs={'pk': self.license.pk})
        response = self.client.post(url, {
            'name': 'Updated License',
            'software': self.software.pk,
            'license_type': LicenseTypeChoices.PERPETUAL_SEAT,
            'seats': 100,
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.license.refresh_from_db()
        self.assertEqual(self.license.seats, 100)

    def test_license_delete_view_post(self):
        url = reverse('licenses:license_delete', kwargs={'pk': self.license.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(License.objects.filter(pk=self.license.pk).exists())
