"""Phase-1 cross-tenant boundary tests for ContactAssignment (F2).

ContactAssignment is a generic-FK assignment model with no direct or relational
`tenant` field (Contact itself is not tenant-scoped). Before the fix its API
LIST returned all-tenant rows and StrictTenantPermission passed through because
the model exposed no `tenant`. The fix scopes the LIST per content type in
ContactAssignmentViewSet.get_queryset() and adds a `tenant` property resolving
the generic-FK target's tenant so StrictTenantPermission can enforce the
object-level boundary on detail/mutation.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from organization.models import (
    Tenant, TenantRole, TenantMembership, Contact, ContactRole, ContactAssignment,
)
from assets.models import (
    Asset, AssetAssignment, StatusLabel, AssetRole, Manufacturer, AssetType,
)
from software.models import Software

User = get_user_model()


class ContactAssignmentCrossTenantTestCase(TestCase):
    def setUp(self):
        # Two tenants
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Users
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        # Roles + memberships. ContactAssignment perms (view/delete) are required
        # for the API to reach the queryset/object-permission layer.
        ca_perms = [
            'organization.view_contactassignment',
            'organization.add_contactassignment',
            'organization.change_contactassignment',
            'organization.delete_contactassignment',
        ]
        self.role_a = TenantRole.objects.create(
            tenant=self.tenant_a, name='Admin', permissions=ca_perms
        )
        self.membership_a = TenantMembership.objects.create(
            user=self.user_a, tenant=self.tenant_a,
        )
        self.membership_a.roles.add(self.role_a)
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b, name='Admin', permissions=ca_perms
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b,
        )
        self.membership_b.roles.add(self.role_b)

        # Shared asset metadata. Use names/slugs unique to this test: the names
        # are uniquely constrained among active rows, and sibling tests in the
        # full suite (run before this one) commit common names like "Active" /
        # "Technical Support", which would otherwise collide in setUp.
        self.status = StatusLabel.objects.create(name='P1CT Active', slug='p1ct-active')
        self.asset_role = AssetRole.objects.create(name='P1CT Laptop', slug='p1ct-laptop')
        self.mfr = Manufacturer.objects.create(name='P1CT Apple', slug='p1ct-apple')
        self.asset_type = AssetType.objects.create(manufacturer=self.mfr, model='P1CT MacBook')

        # One asset per tenant
        self.asset_a = Asset.objects.create(
            name='Asset of A', asset_tag='TAG-A-001', status=self.status,
            asset_role=self.asset_role, asset_type=self.asset_type, tenant=self.tenant_a,
        )
        self.asset_b = Asset.objects.create(
            name='Asset of B', asset_tag='TAG-B-001', status=self.status,
            asset_role=self.asset_role, asset_type=self.asset_type, tenant=self.tenant_b,
        )

        # Contacts (Contact has no tenant field — tenant comes from the GFK target)
        self.contact_a = Contact.objects.create(name='P1CT Contact A', email='p1ct-a@example.com')
        self.contact_b = Contact.objects.create(name='P1CT Contact B', email='p1ct-b@example.com')
        self.contact_role = ContactRole.objects.create(name='P1CT Support Role')

        asset_ct = ContentType.objects.get_for_model(Asset)

        # Assignment scoped to tenant A (target asset belongs to tenant A)
        self.assignment_a = ContactAssignment.objects.create(
            contact=self.contact_a, role=self.contact_role,
            content_type=asset_ct, object_id=self.asset_a.pk, priority='primary',
        )
        # Assignment scoped to tenant B (target asset belongs to tenant B)
        self.assignment_b = ContactAssignment.objects.create(
            contact=self.contact_b, role=self.contact_role,
            content_type=asset_ct, object_id=self.asset_b.pk, priority='primary',
        )

        self.list_url = reverse('api:organization_api:contactassignment-list')

    def _login_tenant_a(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

    def _login_tenant_b(self):
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_b.pk
        session.save()

    def test_list_excludes_other_tenant_assignment(self):
        """Tenant B's API list returns only its own assignment, not tenant A's."""
        self._login_tenant_b()
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        returned_ids = {row['id'] for row in response.json()['results']}
        self.assertIn(self.assignment_b.pk, returned_ids)
        self.assertNotIn(self.assignment_a.pk, returned_ids)

    def test_detail_other_tenant_assignment_404(self):
        """Tenant B GET on tenant A's assignment detail is 404 (no enumeration)."""
        self._login_tenant_b()
        detail_url = reverse(
            'api:organization_api:contactassignment-detail',
            kwargs={'pk': self.assignment_a.pk},
        )
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 404)

    def test_delete_other_tenant_assignment_404_and_survives(self):
        """Tenant B DELETE on tenant A's assignment is 404 and the row survives."""
        self._login_tenant_b()
        detail_url = reverse(
            'api:organization_api:contactassignment-detail',
            kwargs={'pk': self.assignment_a.pk},
        )
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            ContactAssignment.objects.filter(pk=self.assignment_a.pk).exists()
        )

    def test_list_excludes_other_tenant_via_tenant_lookup_target(self):
        """H3 regression: a target with NO `tenant` column whose tenant is derived
        through a `tenant_lookup` (AssetAssignment, `asset__tenant`) must NOT leak.

        The old `get_field('tenant')` probe raised FieldDoesNotExist for such
        models and fell through to `allowed |= Q(content_type=ct)`, exposing every
        AssetAssignment-targeted ContactAssignment to every tenant.
        """
        # AssetAssignment has no `tenant` column; its tenant is asset.tenant.
        # Created inactive (no assignee) — valid per clean()/CheckConstraint and
        # still a tenant_lookup target carrying asset.tenant.
        assignment_target_a = AssetAssignment.objects.create(asset=self.asset_a, is_active=False)
        assignment_target_b = AssetAssignment.objects.create(asset=self.asset_b, is_active=False)
        aa_ct = ContentType.objects.get_for_model(AssetAssignment)
        ca_to_a = ContactAssignment.objects.create(
            contact=self.contact_a, role=self.contact_role,
            content_type=aa_ct, object_id=assignment_target_a.pk, priority='primary',
        )
        ca_to_b = ContactAssignment.objects.create(
            contact=self.contact_b, role=self.contact_role,
            content_type=aa_ct, object_id=assignment_target_b.pk, priority='primary',
        )

        self._login_tenant_b()
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        returned_ids = {row['id'] for row in response.json()['results']}
        # Tenant B sees its own tenant_lookup-scoped assignment...
        self.assertIn(ca_to_b.pk, returned_ids)
        # ...but NOT tenant A's (this leaked before the signal-probe fix).
        self.assertNotIn(ca_to_a.pk, returned_ids)

    def test_list_includes_global_catalogue_target(self):
        """L1: a ContactAssignment pointing at a global (tenant=None) catalogue
        target must appear in the active tenant's list.

        The old code resolved ids with `.filter(tenant=active_tenant)`, which
        dropped legitimate global-catalogue rows. The tenant-scoping default
        manager unions own + global rows, so they must stay visible.
        """
        global_software = Software.objects.create(
            name='P1CT Global Tool', manufacturer=self.mfr, tenant=None,
        )
        sw_ct = ContentType.objects.get_for_model(Software)
        ca_global = ContactAssignment.objects.create(
            contact=self.contact_a, role=self.contact_role,
            content_type=sw_ct, object_id=global_software.pk, priority='primary',
        )

        self._login_tenant_a()
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        returned_ids = {row['id'] for row in response.json()['results']}
        self.assertIn(ca_global.pk, returned_ids)
