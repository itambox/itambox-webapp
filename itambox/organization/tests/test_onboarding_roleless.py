"""Regression tests for role-less technician onboarding (RBAC review §3-E, defect #6).

``TechnicianQuickAddView.form_valid`` must NOT show a plain success toast when the admin
onboards a technician without picking a role — that leaves a membership with zero role
assignments. Instead it warns and deep-links to role creation scoped to the managing
(``is_provider``) tenant. When a role IS chosen the existing success + redirect-to-
membership-detail behaviour is preserved.

Post-collapse world: the ``Provider`` model is gone — a managing organization is a
``Tenant`` with ``is_provider=True``; customers point at it via ``Tenant.managed_by``.
Grants are per-``RoleAssignment`` rows (reach='own' | 'managed'), not a ``Membership.roles``
M2M. The onboarding form's container field is named ``organization`` and the deep-link
carries ``?tenant=<msp pk>`` (not ``?provider=``).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin, grant
from organization.models import Tenant, Membership, Role, RoleAssignment

User = get_user_model()


class RolelessOnboardingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        # A managing (MSP) tenant with one managed customer tenant.
        self.msp = Tenant.objects.create(name="Acme MSP", slug="acme-msp", is_provider=True)
        self.customer = Tenant.objects.create(
            name="Customer One", slug="customer-one", managed_by=self.msp,
        )

        # A role at the MSP tenant that grants the staff admin the permissions the
        # onboarding view/form gate on: organization.add_membership (view test_func +
        # form_valid defense-in-depth) and organization.add_roleassignment (the
        # managed-reach escalation guard in validate_assignment_grant).
        # Includes assets.view_asset so the admin actually HOLDS the technician role's
        # permission — otherwise the escalation guard would (correctly) block granting
        # it, which is a separate concern from the role-less-vs-role-bearing branch
        # under test.
        self.admin_role = Role.objects.create(
            tenant=self.msp,
            name="Provider Admin",
            permissions=[
                "organization.add_membership",
                "organization.add_roleassignment",
                "assets.view_asset",
            ],
        )
        # A separate role the admin can hand to a new technician (its permission is a
        # subset of the admin's, so the grant is not an escalation).
        self.tech_role = Role.objects.create(
            tenant=self.msp, name="Technician", permissions=["assets.view_asset"],
        )

        # The acting user: a non-superuser MSP staff member holding the admin role at
        # the MSP tenant, both directly (own reach, for the perm checks above) and with
        # managed reach over ALL managed tenants (so the escalation guard lets them hand
        # out SCOPE_ALL coverage to the new hire).
        self.staff_admin = User.objects.create_user(
            username="staff_admin", email="staff_admin@example.com",
            password="pw", is_active=True,
        )
        grant(self.staff_admin, self.msp, self.admin_role, reach=RoleAssignment.REACH_OWN)
        grant(
            self.staff_admin, self.msp, self.admin_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
        )

        self.url = reverse('organization:technician_quick_add')

    def tearDown(self):
        self.clear_tenant_context()

    def _login(self):
        self.client.force_login(self.staff_admin)

    def _base_post_data(self):
        return {
            'email': 'newtech@example.com',
            'first_name': 'New',
            'last_name': 'Tech',
            'organization': self.msp.pk,
            'managed_scope': RoleAssignment.SCOPE_ALL,
        }

    def test_roleless_onboarding_warns_and_redirects_to_role_creation(self):
        self._login()
        data = self._base_post_data()
        # No 'role' key -> role is None (allowed for a first hire).
        resp = self.client.post(self.url, data)

        # The membership was created but carries no role assignments yet.
        membership = Membership.objects.get(
            user__email='newtech@example.com', tenant=self.msp,
        )
        self.assertEqual(membership.assignments.count(), 0)

        # Redirects toward role creation scoped to the MSP tenant, not the membership
        # detail.
        self.assertEqual(resp.status_code, 302)
        expected = reverse('organization:role_create') + f'?tenant={self.msp.pk}'
        self.assertEqual(resp.url, expected)
        self.assertNotIn(
            reverse('organization:membership_detail', kwargs={'pk': membership.pk}),
            resp.url,
        )

        # A warning message (not success) was queued.
        messages = list(resp.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'warning')
        self.assertIn('NO', str(messages[0]))

    def test_onboarding_with_role_succeeds_and_redirects_to_membership_detail(self):
        self._login()
        data = self._base_post_data()
        data['role'] = self.tech_role.pk
        resp = self.client.post(self.url, data)

        membership = Membership.objects.get(
            user__email='newtech@example.com', tenant=self.msp,
        )
        self.assertTrue(
            RoleAssignment.objects.filter(
                membership=membership, role=self.tech_role,
                reach=RoleAssignment.REACH_MANAGED,
            ).exists()
        )

        # Redirects to the membership detail with a success message.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse('organization:membership_detail', kwargs={'pk': membership.pk}),
        )

        messages = list(resp.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'success')
