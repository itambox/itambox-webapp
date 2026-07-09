"""Regression tests for role-less technician onboarding (RBAC review §3-E, defect #6).

``TechnicianQuickAddView.form_valid`` must NOT show a plain success toast when the admin
onboards a technician without picking a role — that leaves a zero-permission membership.
Instead it warns and deep-links to provider-scoped role creation. When a role IS chosen the
existing success + redirect-to-membership-detail behaviour is preserved.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from organization.models import Provider, Tenant, Membership, Role

User = get_user_model()


class RolelessOnboardingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        # A provider (MSP) with one customer tenant.
        self.provider = Provider.objects.create(name="Acme MSP", slug="acme-msp")
        self.customer = Tenant.objects.create(
            name="Customer One", slug="customer-one", provider=self.provider,
        )

        # A provider-scoped role that grants the staff admin the capabilities the
        # onboarding view gates on: manage_provider (view access via ProviderAdminMixin)
        # and manage_staff (the form_valid permission check).
        # Includes assets.view_asset so the admin actually HOLDS the technician role's
        # permission — otherwise the escalation guard would (correctly) block granting it,
        # which is a separate concern from the role-less-vs-role-bearing branch under test.
        self.admin_role = Role.objects.create(
            provider=self.provider,
            scope=Role.SCOPE_PROVIDER,
            name="Provider Admin",
            permissions=[
                "organization.manage_provider",
                "organization.manage_staff",
                "assets.view_asset",
            ],
        )
        # A separate provider-scoped role the admin can hand to a new technician (its
        # permission is a subset of the admin's, so the grant is not an escalation).
        self.tech_role = Role.objects.create(
            provider=self.provider,
            scope=Role.SCOPE_PROVIDER,
            name="Technician",
            permissions=["assets.view_asset"],
        )

        # The acting user: a non-superuser provider staff member holding the admin role.
        self.staff_admin = User.objects.create_user(
            username="staff_admin", email="staff_admin@example.com",
            password="pw", is_active=True,
        )
        self.admin_membership = Membership.objects.create(
            user=self.staff_admin, provider=self.provider,
            tenant_scope=Membership.SCOPE_ALL, is_active=True,
        )
        self.admin_membership.roles.add(self.admin_role)

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
            'provider': self.provider.pk,
            'tenant_scope': Membership.SCOPE_ALL,
        }

    def test_roleless_onboarding_warns_and_redirects_to_role_creation(self):
        self._login()
        data = self._base_post_data()
        # No 'role' key -> role is None (allowed for a first hire).
        resp = self.client.post(self.url, data)

        # The membership was created but carries no roles yet.
        membership = Membership.objects.get(
            user__email='newtech@example.com', provider=self.provider,
        )
        self.assertEqual(membership.roles.count(), 0)

        # Redirects toward provider-scoped role creation, not the membership detail.
        self.assertEqual(resp.status_code, 302)
        expected = reverse('organization:role_create') + f'?provider={self.provider.pk}'
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
            user__email='newtech@example.com', provider=self.provider,
        )
        self.assertIn(self.tech_role, membership.roles.all())

        # Redirects to the membership detail with a success message.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse('organization:membership_detail', kwargs={'pk': membership.pk}),
        )

        messages = list(resp.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'success')
