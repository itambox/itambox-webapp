from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from software.models import Software
from assets.models import Manufacturer
from .models import License, LicenseTypeChoices

User = get_user_model()

class LicenseViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Create user and log in
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')

        # Create prerequisite models
        self.manufacturer = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        self.software = Software.objects.create(
            name="Office 365 Enterprise",
            manufacturer=self.manufacturer,
            description="Office suite for enterprise"
        )

        # Create a test license
        self.license = License.objects.create(
            name="Office 365 E5 Renewal FY26",
            software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            seats=50,
            product_key="XXXX-XXXX-XXXX-XXXX"
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


class LicenseEncryptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        self.software = Software.objects.create(
            name="Windows 11 Enterprise",
            manufacturer=self.manufacturer,
            description="OS"
        )

    def test_license_product_key_encryption_lifecycle(self):
        # 1. Create a license with a plaintext product key
        raw_key = "ABCDE-12345-FGHIJ-67890"
        license_obj = License.objects.create(
            name="Windows 11 Volume entitlement",
            software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            seats=10,
            product_key=raw_key
        )

        # 2. Verify it is encrypted in the database (i.e. starts with enc$)
        # Refresh from database directly bypassing properties to check raw database value
        db_record = License.objects.get(pk=license_obj.pk)
        self.assertTrue(db_record.product_key.startswith("enc$"))
        self.assertNotEqual(db_record.product_key, raw_key)

        # 3. Verify it is decrypted seamlessly via the model property
        self.assertEqual(db_record.decrypted_product_key, raw_key)

        # 4. Verify detail view displays the decrypted key
        detail_url = reverse('licenses:license_detail', kwargs={'pk': license_obj.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, raw_key)
        self.assertNotContains(response, db_record.product_key) # Cipher text shouldn't be exposed

        # 5. Verify edit form loads the decrypted key as initial value
        edit_url = reverse('licenses:license_update', kwargs={'pk': license_obj.pk})
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        # Verify the form instance has the correct initial value
        form = response.context['form']
        self.assertEqual(form.fields['product_key'].initial, raw_key)

    def test_plaintext_fallback_backwards_compatibility(self):
        # 1. Manually insert a raw plaintext key into the database (bypassing model .save())
        raw_key = "LEGACY-PLAINTEXT-KEY"
        license_id = License.objects.create(
            name="Legacy Office 2019",
            software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT,
            seats=5
        ).pk
        
        # Perform raw update to bypass the custom overridden .save() method
        License.objects.filter(pk=license_id).update(product_key=raw_key)

        # 2. Verify that retrieval returns the plaintext key perfectly via the property (backwards-compatibility)
        db_record = License.objects.get(pk=license_id)
        self.assertEqual(db_record.product_key, raw_key)
        self.assertEqual(db_record.decrypted_product_key, raw_key)

        # 3. Verify details view successfully displays the plaintext key without errors
        detail_url = reverse('licenses:license_detail', kwargs={'pk': license_id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, raw_key)

        # 4. Save the license again, and verify it automatically encrypts the plaintext key at rest!
        db_record.save()
        db_record.refresh_from_db()
        self.assertTrue(db_record.product_key.startswith("enc$"))
        self.assertEqual(db_record.decrypted_product_key, raw_key)

