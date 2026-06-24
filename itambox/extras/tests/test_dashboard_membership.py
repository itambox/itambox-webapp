"""Regression tests for WS1-5: dashboards may only be bound to tenants the
user is a member of (DashboardCreateView), and the manage-modal tenant dropdown
is scoped to the user's memberships. Superusers keep the global view."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from organization.models import Tenant, TenantRole, TenantMembership
from extras.models import Dashboard

User = get_user_model()


class DashboardCreateMembershipTests(TestCase):
    def setUp(self):
        self.tenant_home = Tenant.objects.create(name="Home Corp", slug="home-corp")
        self.tenant_foreign = Tenant.objects.create(name="Foreign Corp", slug="foreign-corp")

        self.role = TenantRole.objects.create(tenant=self.tenant_home, name="Member", permissions=[])

        self.member = User.objects.create_user(username="member", password="password")
        m = TenantMembership.objects.create(user=self.member, tenant=self.tenant_home)
        m.roles.add(self.role)

        self.superuser = User.objects.create_superuser(username="root", password="password")

    def test_non_member_cannot_bind_dashboard_to_foreign_tenant(self):
        # A user who is not a member of the foreign tenant POSTs its id directly.
        # The create form submits via HTMX, so mirror that with HTTP_HX_REQUEST.
        self.client.login(username="member", password="password")
        url = reverse('extras:dashboard_create')
        response = self.client.post(url, {
            'name': 'Sneaky Board',
            'tenant': self.tenant_foreign.id,
        }, HTTP_HX_REQUEST='true')

        # Rejected (forbidden) and no dashboard bound to the foreign tenant.
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Dashboard.objects.filter(tenant=self.tenant_foreign).exists()
        )
        self.assertFalse(
            Dashboard.objects.filter(user=self.member, name='Sneaky Board').exists()
        )

    def test_non_member_rejected_on_plain_post_no_dashboard_bound(self):
        # Same boundary on a non-HTMX POST: a redirect (no HX header) but still
        # no dashboard bound to the foreign tenant.
        self.client.login(username="member", password="password")
        url = reverse('extras:dashboard_create')
        response = self.client.post(url, {
            'name': 'Sneaky Board 2',
            'tenant': self.tenant_foreign.id,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Dashboard.objects.filter(tenant=self.tenant_foreign).exists()
        )

    def test_member_can_create_dashboard_for_own_tenant(self):
        self.client.login(username="member", password="password")
        url = reverse('extras:dashboard_create')
        response = self.client.post(url, {
            'name': 'Home Board',
            'tenant': self.tenant_home.id,
        })

        self.assertRedirects(response, reverse('dashboard'))
        board = Dashboard.objects.filter(user=self.member, name='Home Board').first()
        self.assertIsNotNone(board)
        self.assertEqual(board.tenant, self.tenant_home)

    def test_superuser_can_bind_dashboard_to_any_tenant(self):
        # Superusers keep the global view: binding to a tenant they are not a
        # member of is allowed.
        self.client.login(username="root", password="password")
        url = reverse('extras:dashboard_create')
        response = self.client.post(url, {
            'name': 'Admin Board',
            'tenant': self.tenant_foreign.id,
        })

        self.assertRedirects(response, reverse('dashboard'))
        board = Dashboard.objects.filter(user=self.superuser, name='Admin Board').first()
        self.assertIsNotNone(board)
        self.assertEqual(board.tenant, self.tenant_foreign)


class DashboardManageModalScopingTests(TestCase):
    def setUp(self):
        self.tenant_home = Tenant.objects.create(name="Home Corp", slug="home-corp")
        self.tenant_foreign = Tenant.objects.create(name="Foreign Corp", slug="foreign-corp")

        self.role = TenantRole.objects.create(tenant=self.tenant_home, name="Member", permissions=[])

        self.member = User.objects.create_user(username="member", password="password")
        m = TenantMembership.objects.create(user=self.member, tenant=self.tenant_home)
        m.roles.add(self.role)

        self.superuser = User.objects.create_superuser(username="root", password="password")

    def test_manage_modal_dropdown_scoped_to_memberships(self):
        self.client.login(username="member", password="password")
        response = self.client.get(reverse('extras:dashboard_manage_modal'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Member sees only their own tenant, never the foreign one.
        self.assertIn("Home Corp", body)
        self.assertNotIn("Foreign Corp", body)

    def test_manage_modal_dropdown_global_for_superuser(self):
        self.client.login(username="root", password="password")
        response = self.client.get(reverse('extras:dashboard_manage_modal'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Superuser keeps the global view across all tenants.
        self.assertIn("Home Corp", body)
        self.assertIn("Foreign Corp", body)
