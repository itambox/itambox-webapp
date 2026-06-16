from django.test import TestCase
from django.urls import reverse
from model_bakery import baker
from django.contrib.auth import get_user_model

from software.models import Software
from ..models import License, LicenseTypeChoices

User = get_user_model()


class LicenseCloneViewTests(TestCase):
    """Regression tests for FIX C9: the clone view must not persist an orphan
    License row on GET, and must reset product_key on the created clone."""

    def setUp(self):
        self.user = baker.make(User, is_staff=True, is_superuser=True)
        self.user.set_password('testpassword')
        self.user.save()
        self.client.login(username=self.user.username, password='testpassword')

        self.software = baker.make(
            Software,
            name="Office 365 Enterprise",
            manufacturer__name="Microsoft",
            manufacturer__slug="microsoft",
        )
        self.license = baker.make(
            License,
            name="Office 365 E5 Renewal FY26",
            software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            seats=50,
            product_key="XXXX-XXXX-XXXX-XXXX",
            tenant=None,
        )

    def test_clone_get_does_not_create_orphan(self):
        """GET on the clone URL pre-fills the form without persisting a row."""
        count_before = License.objects.count()
        url = reverse('licenses:license_clone', kwargs={'pk': self.license.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(License.objects.count(), count_before)

    def test_clone_post_creates_one_license_with_blank_product_key(self):
        """POST creates exactly one new License and resets product_key."""
        count_before = License.objects.count()
        url = reverse('licenses:license_clone', kwargs={'pk': self.license.pk})
        response = self.client.post(url, {
            'name': 'Office 365 E5 Renewal FY26 (Copy)',
            'software': self.software.pk,
            'license_type': LicenseTypeChoices.PERPETUAL_SEAT,
            'seats': 50,
            'product_key': '',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertEqual(License.objects.count(), count_before + 1)
        clone = License.objects.exclude(pk=self.license.pk).get()
        self.assertEqual(clone.product_key, '')
