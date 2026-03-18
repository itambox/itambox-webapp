from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from software.models import Software
from assets.models import Manufacturer, AssetRole, Asset
from organization.models import AssetHolder
from .models import License, LicenseTypeChoices, LicenseSeatAssignment

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



class LicenseSeatAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.manufacturer = Manufacturer.objects.create(name='Microsoft', slug='microsoft')
        self.software = Software.objects.create(name='Windows 11', manufacturer=self.manufacturer)
        self.role = AssetRole.objects.create(name='Workstation', slug='workstation')
        self.asset = Asset.objects.create(
            name='WS-01', asset_tag='TAG-WS-01', asset_role=self.role,
        )
        self.holder = AssetHolder.objects.create(
            first_name='John', last_name='Doe', upn='john.doe',
        )

    def test_seat_assignment_to_asset(self):
        license_obj = License.objects.create(
            name='Win11 Volume', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=10,
        )
        assignment = LicenseSeatAssignment.objects.create(
            license=license_obj, asset=self.asset,
        )
        self.assertEqual(assignment.license, license_obj)
        self.assertEqual(assignment.asset, self.asset)

    def test_seat_assignment_to_holder(self):
        license_obj = License.objects.create(
            name='Win11 User', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=5,
        )
        assignment = LicenseSeatAssignment.objects.create(
            license=license_obj, assigned_holder=self.holder,
        )
        self.assertEqual(assignment.assigned_holder, self.holder)

    def test_seat_assignment_single_target_constraint(self):
        license_obj = License.objects.create(
            name='Win11 Constraint', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=10,
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            LicenseSeatAssignment.objects.create(
                license=license_obj, asset=self.asset, assigned_holder=self.holder,
            )

    def test_available_seats(self):
        license_obj = License.objects.create(
            name='Win11 5 Seats', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=5,
        )
        self.assertEqual(license_obj.available_seats, 5)
        LicenseSeatAssignment.objects.create(license=license_obj, asset=self.asset)
        self.assertEqual(license_obj.available_seats, 4)

    def test_license_expiration(self):
        from datetime import date, timedelta
        yesterday = date.today() - timedelta(days=1)
        license_obj = License.objects.create(
            name='Expired License', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=1,
            expiration_date=yesterday,
        )
        self.assertIsNotNone(license_obj.expiration_date)
        self.assertLess(license_obj.expiration_date, date.today())

    def test_license_future_expiration(self):
        from datetime import date, timedelta
        future = date.today() + timedelta(days=365)
        license_obj = License.objects.create(
            name='Active License', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=1,
            expiration_date=future,
        )
        self.assertGreater(license_obj.expiration_date, date.today())

    def test_seat_assignment_soft_delete(self):
        license_obj = License.objects.create(
            name='SoftDelete Seat', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=2,
        )
        assignment = LicenseSeatAssignment.objects.create(
            license=license_obj, asset=self.asset,
        )
        self.assertEqual(license_obj.available_seats, 1)
        assignment.delete()
        self.assertEqual(license_obj.available_seats, 2)

    def test_seat_assignment_absolute_url(self):
        license_obj = License.objects.create(
            name='URL Test', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=1,
        )
        assignment = LicenseSeatAssignment.objects.create(
            license=license_obj, asset=self.asset,
        )
        url = assignment.get_absolute_url()
        self.assertIsNotNone(url)

    def test_seat_assignment_str(self):
        license_obj = License.objects.create(
            name='Str Test', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=1,
        )
        assignment = LicenseSeatAssignment.objects.create(
            license=license_obj, asset=self.asset,
        )
        self.assertIn('Str Test', str(assignment))

    def test_license_edit_view_post(self):
        license_obj = License.objects.create(
            name='Edit Test', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=50,
        )
        url = reverse('licenses:license_update', kwargs={'pk': license_obj.pk})
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
        license_obj.refresh_from_db()
        self.assertEqual(license_obj.seats, 100)

    def test_license_delete_view_post(self):
        license_obj = License.objects.create(
            name='To Delete', software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=1,
        )
        url = reverse('licenses:license_delete', kwargs={'pk': license_obj.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(License.objects.filter(pk=license_obj.pk).exists())

