"""H2: API create-boundary tests for the licenses app.

Two distinct boundary failures are covered:

1. Cross-tenant create via related-PK references. A tenant-B member POSTing a
   seat assignment that references tenant-A's License / AssetHolder must be
   rejected. The fix converts the serializer ``PrimaryKeyRelatedField`` querysets
   from import-time ``Model.objects.all()`` (a frozen, unscoped queryset) to the
   bare ``Model.objects`` manager, so DRF re-runs the tenant filter at request
   time and the cross-tenant PKs no longer validate.

2. Global-row minting. A tenant-bound (non-superuser) request that omits
   ``tenant_id`` must NOT mint a global (tenant=None) License visible to every
   tenant; ``perform_create`` defaults the row to the active tenant.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from organization.models import Tenant, TenantRole, TenantMembership, AssetHolder
from assets.models import Manufacturer
from software.models import Software
from licenses.models import License, LicenseSeatAssignment, LicenseTypeChoices

User = get_user_model()


class SeatCreateCrossTenantTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['licenses.add_licenseseatassignment', 'licenses.view_licenseseatassignment'],
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b, role=self.role_b
        )

        self.mfr = Manufacturer.objects.create(name='Microsoft', slug='microsoft')

        # tenant-A parent objects the attacker will try to reference.
        self.software_a = Software.objects.create(
            name='Office A', manufacturer=self.mfr, tenant=self.tenant_a
        )
        self.license_a = License.objects.create(
            name='License A', software=self.software_a,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=10, tenant=self.tenant_a,
        )
        self.holder_a = AssetHolder.objects.create(
            first_name='Alice', last_name='A', upn='alice@a.example.com', tenant=self.tenant_a
        )

    def _activate(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

    def test_seat_create_referencing_other_tenant_parents_is_rejected(self):
        self._activate(self.user_b, self.tenant_b)
        url = reverse('api:licenses_api:licenseseatassignment-list')
        response = self.client.post(
            url,
            data={
                'license_id': self.license_a.pk,
                'assigned_holder_id': self.holder_a.pk,
            },
            content_type='application/json',
        )
        # The tenant-A license_id/assigned_holder_id are no longer valid choices
        # in tenant-B's request-scoped queryset.
        self.assertIn(response.status_code, (400, 404))
        # No cross-tenant seat was created.
        self.assertFalse(
            LicenseSeatAssignment.all_objects.filter(license=self.license_a).exists()
        )


class GlobalLicenseMintTests(TestCase):
    def setUp(self):
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')
        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['licenses.add_license', 'licenses.view_license'],
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b, role=self.role_b
        )
        self.mfr = Manufacturer.objects.create(name='Microsoft', slug='microsoft')
        self.software_b = Software.objects.create(
            name='Office B', manufacturer=self.mfr, tenant=self.tenant_b
        )

    def _activate(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

    def test_create_without_tenant_defaults_to_active_tenant(self):
        self._activate(self.user_b, self.tenant_b)
        url = reverse('api:licenses_api:license-list')
        response = self.client.post(
            url,
            data={
                'name': 'New License',
                'software_id': self.software_b.pk,
                'license_type': LicenseTypeChoices.PERPETUAL_SEAT,
                'seats': 5,
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        created = License.all_objects.get(pk=response.json()['id'])
        # Must NOT be a global (tenant=None) row — it is bound to the active tenant.
        self.assertEqual(created.tenant, self.tenant_b)
