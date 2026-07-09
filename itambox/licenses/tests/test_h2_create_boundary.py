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

from organization.models import Tenant, Role, Membership, AssetHolder
from assets.models import Manufacturer, StatusLabel, Asset
from software.models import Software
from licenses.models import License, LicenseSeatAssignment, LicenseTypeChoices

User = get_user_model()


class SeatCreateCrossTenantTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.role_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['licenses.add_licenseseatassignment', 'licenses.view_licenseseatassignment'],
        )
        self.membership_b = Membership.objects.create(user=self.user_b, tenant=self.tenant_b,
        )
        self.membership_b.roles.add(self.role_b)

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
        self.role_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['licenses.add_license', 'licenses.view_license'],
        )
        self.membership_b = Membership.objects.create(user=self.user_b, tenant=self.tenant_b,
        )
        self.membership_b.roles.add(self.role_b)
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


class SeatOverAllocationTests(TestCase):
    """WS2-1: the REST seat-assignment CRUD path must enforce the same availability +
    no-duplicate-target invariants as checkout_license(); previously it could over-allocate."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Tenant', slug='t-seat')
        self.user = User.objects.create_user(username='seatuser', password='pw')
        role = Role.objects.create(
            tenant=self.tenant, name='Admin',
            permissions=[
                'licenses.add_licenseseatassignment', 'licenses.view_licenseseatassignment',
                'licenses.change_licenseseatassignment',
            ],
        )
        m = Membership.objects.create(user=self.user, tenant=self.tenant)
        m.roles.add(role)
        self.mfr = Manufacturer.objects.create(name='MS', slug='ms-seat')
        self.software = Software.objects.create(name='Office', manufacturer=self.mfr, tenant=self.tenant)
        self.status = StatusLabel.objects.create(name='Deployable Seat', slug='dep-seat', type='deployable')
        self.asset1 = Asset.objects.create(name='A1', asset_tag='SEAT-A1', status=self.status, tenant=self.tenant)
        self.asset2 = Asset.objects.create(name='A2', asset_tag='SEAT-A2', status=self.status, tenant=self.tenant)

    def _activate(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_cannot_over_allocate_seats_via_api(self):
        self._activate()
        url = reverse('api:licenses_api:licenseseatassignment-list')
        one_seat = License.objects.create(
            name='OneSeat', software=self.software, seats=1, tenant=self.tenant,
        )
        r1 = self.client.post(
            url, {'license_id': one_seat.pk, 'asset_id': self.asset1.pk}, content_type='application/json'
        )
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.client.post(
            url, {'license_id': one_seat.pk, 'asset_id': self.asset2.pk}, content_type='application/json'
        )
        self.assertEqual(r2.status_code, 400, r2.content)
        self.assertEqual(LicenseSeatAssignment.objects.filter(license=one_seat).count(), 1)

    def test_cannot_over_allocate_seats_via_patch_transfer(self):
        # D5-1: the seat-capacity guard was create-only. A PATCH repointing
        # license_id at a different (fully-seated) License bypassed it entirely —
        # default ModelSerializer.update() just reassigned the FK and saved.
        self._activate()
        list_url = reverse('api:licenses_api:licenseseatassignment-list')
        license_a = License.objects.create(
            name='LicenseA', software=self.software, seats=1, tenant=self.tenant,
        )
        license_b = License.objects.create(
            name='LicenseB', software=self.software, seats=1, tenant=self.tenant,
        )
        r1 = self.client.post(
            list_url, {'license_id': license_a.pk, 'asset_id': self.asset1.pk}, content_type='application/json'
        )
        self.assertEqual(r1.status_code, 201, r1.content)
        assignment_a_id = r1.json()['id']
        etag = r1['ETag']
        r2 = self.client.post(
            list_url, {'license_id': license_b.pk, 'asset_id': self.asset2.pk}, content_type='application/json'
        )
        self.assertEqual(r2.status_code, 201, r2.content)

        detail_url = reverse('api:licenses_api:licenseseatassignment-detail', kwargs={'pk': assignment_a_id})
        patch_resp = self.client.patch(
            detail_url, {'license_id': license_b.pk}, content_type='application/json', HTTP_IF_MATCH=etag
        )
        self.assertEqual(patch_resp.status_code, 400, patch_resp.content)
        self.assertEqual(LicenseSeatAssignment.objects.filter(license=license_b).count(), 1)

    def test_cannot_assign_same_asset_twice(self):
        self._activate()
        url = reverse('api:licenses_api:licenseseatassignment-list')
        two_seat = License.objects.create(
            name='TwoSeat', software=self.software, seats=2, tenant=self.tenant,
        )
        r1 = self.client.post(
            url, {'license_id': two_seat.pk, 'asset_id': self.asset1.pk}, content_type='application/json'
        )
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.client.post(
            url, {'license_id': two_seat.pk, 'asset_id': self.asset1.pk}, content_type='application/json'
        )
        self.assertEqual(r2.status_code, 400, r2.content)
        self.assertEqual(LicenseSeatAssignment.objects.filter(license=two_seat).count(), 1)
