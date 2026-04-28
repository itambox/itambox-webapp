from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer
from organization.models import AssetHolder, Site, Location, Tenant
from licenses.models import License, LicenseSeatAssignment
from software.models import Software

User = get_user_model()


class ITAMBoxAPITestCase(APITestCase):
    def setUp(self):
        # Create users
        self.superuser = User.objects.create_user(
            username='superuser', email='super@example.com', password='password123', is_staff=True, is_superuser=True
        )
        self.staff = User.objects.create_user(
            username='staff', email='staff@example.com', password='password123', is_staff=True, is_superuser=False
        )

        # Tenants
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Associate staff with Tenant A via AssetHolder profile
        self.holder_staff = AssetHolder.objects.create(
            user=self.staff,
            first_name="Staff",
            last_name="User",
            upn="staff@tenant-a.com",
            tenant=self.tenant_a
        )

        self.holder_a = AssetHolder.objects.create(
            first_name="Holder",
            last_name="A",
            upn="holder@tenant-a.com",
            tenant=self.tenant_a
        )
        self.holder_b = AssetHolder.objects.create(
            first_name="Holder",
            last_name="B",
            upn="holder@tenant-b.com",
            tenant=self.tenant_b
        )

        # Staging Site and Locations
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.location_a = Location.objects.create(name="Staging A", slug="staging-a", site=self.site, tenant=self.tenant_a)
        self.location_b = Location.objects.create(name="Staging B", slug="staging-b", site=self.site, tenant=self.tenant_b)

        # Asset metadata
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.role = AssetRole.objects.create(name="Workstation", slug="workstation")
        self.status = StatusLabel.objects.create(name="Ready", slug="ready", type=StatusLabel.TYPE_DEPLOYABLE)

        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5520",
            slug="latitude-5520"
        )

        # Create tenant-specific assets
        self.asset_a = Asset.objects.create(
            name="Asset A",
            asset_tag="TAG-A",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant_a
        )
        self.asset_b = Asset.objects.create(
            name="Asset B",
            asset_tag="TAG-B",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant_b
        )

        # Software & License Setup
        self.software = Software.objects.create(
            name="Office 2021",
            manufacturer=self.manufacturer
        )

        self.license_a = License.objects.create(
            name="Office 2021 Standard",
            software=self.software,
            seats=10,
            tenant=self.tenant_a
        )

        # Grant view, add, and change permissions to staff user so they can pass TokenPermissions checks
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        
        content_type = ContentType.objects.get_for_model(Asset)
        for codename in ['view_asset', 'add_asset', 'change_asset']:
            permission = Permission.objects.get(
                codename=codename,
                content_type=content_type,
            )
            self.staff.user_permissions.add(permission)

    def test_asset_checkout_and_checkin_actions(self):
        self.client.force_authenticate(user=self.superuser)

        # Test Checkout Action to Holder
        checkout_url = reverse('api:assets_api:asset-checkout', kwargs={'pk': self.asset_a.pk})
        data = {'holder_id': self.holder_a.id, 'notes': 'Checked out to holder A via API'}
        
        response = self.client.post(checkout_url, data=data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')

        # Verify asset is checked out
        self.asset_a.refresh_from_db()
        self.assertIsNotNone(self.asset_a.active_assignment)
        self.assertEqual(self.asset_a.assigned_to, self.holder_a)

        # Test Checkin Action
        checkin_url = reverse('api:assets_api:asset-checkin', kwargs={'pk': self.asset_a.pk})
        data = {'notes': 'Check in asset A via API'}
        
        response = self.client.post(checkin_url, data=data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')

        # Verify asset is checked in
        self.asset_a.refresh_from_db()
        self.assertIsNone(self.asset_a.active_assignment)

    def test_checkout_multiple_targets_fails(self):
        self.client.force_authenticate(user=self.superuser)

        checkout_url = reverse('api:assets_api:asset-checkout', kwargs={'pk': self.asset_a.pk})
        data = {
            'holder_id': self.holder_a.id,
            'location_id': self.location_a.id,
            'notes': 'Should fail'
        }
        
        response = self.client.post(checkout_url, data=data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Should raise validation error
        self.assertIn("You can only check out an asset to ONE target.", str(response.data))

    def test_license_and_assignment_crud_parity(self):
        self.client.force_authenticate(user=self.superuser)

        # 1. Create a License via API
        licenses_url = reverse('api:licenses_api:license-list')
        data = {
            'name': 'API Office License',
            'software_id': self.software.id,
            'seats': 50
        }
        response = self.client.post(licenses_url, data=data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        license_id = response.data['id']
        license_etag = response['ETag']

        # 2. Assign license seat to asset via API
        assigns_url = reverse('api:licenses_api:licenseseatassignment-list')
        assign_data = {
            'license_id': license_id,
            'asset_id': self.asset_a.id
        }
        response = self.client.post(assigns_url, data=assign_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assignment_id = response.data['id']
        assignment_etag = response['ETag']

        # 3. Retrieve assignment details
        assign_detail_url = reverse('api:licenses_api:licenseseatassignment-detail', kwargs={'pk': assignment_id})
        response = self.client.get(assign_detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4. Delete assignment with ETag concurrency check
        response = self.client.delete(assign_detail_url, HTTP_IF_MATCH=assignment_etag)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # 5. Delete License with ETag concurrency check
        license_detail_url = reverse('api:licenses_api:license-detail', kwargs={'pk': license_id})
        response = self.client.delete(license_detail_url, HTTP_IF_MATCH=license_etag)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_strict_tenant_isolation_boundary(self):
        # Authenticate as staff user who belongs to Tenant A
        self.client.force_authenticate(user=self.staff)

        # 1. Accessing asset of Tenant A should succeed
        detail_url_a = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(detail_url_a)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 2. Accessing asset of Tenant B should fail (filtered out by TenantScopingManager)
        detail_url_b = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_b.pk})
        response = self.client.get(detail_url_b)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 3. Mutating/checking out Tenant B's asset should also fail
        checkout_url_b = reverse('api:assets_api:asset-checkout', kwargs={'pk': self.asset_b.pk})
        response = self.client.post(checkout_url_b, data={'holder_id': self.holder_b.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
