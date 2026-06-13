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

    def test_license_detail_seats_tab_renders_assignments(self):
        """The ?tab=seats pane renders seat assignments, and the Asset Holder
        column resolves: directly for holder-seats, and via the asset's current
        holder for asset-seats."""
        from organization.models import AssetHolder
        from assets.models import Asset, AssetAssignment
        # 1. Seat assigned directly to a holder.
        holder = baker.make(AssetHolder, first_name='Jane', last_name='Roe', upn='jane.roe@example.com', tenant=None)
        baker.make('licenses.LicenseSeatAssignment', license=self.license, assigned_holder=holder, asset=None)
        # 2. Seat assigned to an asset that is itself checked out to a holder.
        asset_holder = baker.make(AssetHolder, first_name='Asset', last_name='User', upn='asset.holder@example.com', tenant=None)
        asset = baker.make(Asset, name='REPRO-LAPTOP-01', tenant=None)
        baker.make(AssetAssignment, asset=asset, assigned_user=asset_holder, is_active=True)
        baker.make('licenses.LicenseSeatAssignment', license=self.license, asset=asset, assigned_holder=None)

        url = reverse('licenses:license_detail', kwargs={'pk': self.license.pk}) + '?tab=seats'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'REPRO-LAPTOP-01')
        self.assertContains(response, 'jane.roe@example.com')        # direct holder seat
        self.assertContains(response, 'asset.holder@example.com')    # resolved via the asset

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

    def test_license_checkout_view_get(self):
        """Verify checkout form modal view loads successfully."""
        url = reverse('licenses:license_checkout', kwargs={'pk': self.license.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'licenses/includes/license_checkout_modal.html')

    def test_license_checkout_view_post_holder(self):
        """Verify checking out a license seat to an AssetHolder successfully."""
        holder = baker.make('organization.AssetHolder', first_name='John', last_name='Doe')
        url = reverse('licenses:license_checkout', kwargs={'pk': self.license.pk})
        
        # Test HTTP POST to checkout to holder
        response = self.client.post(url, {
            'target_type': 'holder',
            'assigned_holder': holder.pk,
            'notes': 'Test assign to holder',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.license.available_seats, 49)

    def test_license_checkout_view_post_asset(self):
        """Verify checking out a license seat to an Asset successfully."""
        asset = baker.make('assets.Asset', name='Test Laptop', status__type='deployable')
        url = reverse('licenses:license_checkout', kwargs={'pk': self.license.pk})
        
        # Test HTTP POST to checkout to asset
        response = self.client.post(url, {
            'target_type': 'asset',
            'asset': asset.pk,
            'notes': 'Test assign to asset',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.license.available_seats, 49)

    def test_license_checkin_view_post(self):
        """Verify checking in (deleting) an active license assignment seat."""
        holder = baker.make('organization.AssetHolder', first_name='John', last_name='Doe')
        assignment = baker.make(
            'licenses.LicenseSeatAssignment',
            license=self.license,
            assigned_holder=holder
        )
        self.assertEqual(self.license.available_seats, 49)

        url = reverse('licenses:license_seat_checkin', kwargs={'pk': assignment.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.license.available_seats, 50)
