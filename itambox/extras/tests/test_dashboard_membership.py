"""Dashboard targeting follows canonical tenant reach, including managed MSP access."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import grant
from extras.dashboard.widgets import dashboard_target_tenants
from extras.models import Dashboard
from organization.models import Membership, Role, RoleGrantScope, Tenant

User = get_user_model()


class DashboardCreateMembershipTests(TestCase):
    def setUp(self):
        self.tenant_home = Tenant.objects.create(name="Home Corp", slug="home-corp")
        self.tenant_foreign = Tenant.objects.create(name="Foreign Corp", slug="foreign-corp")

        self.role = Role.objects.create(tenant=self.tenant_home, name="Member", permissions=[])

        self.member = User.objects.create_user(username="member", password="password")
        grant(self.member, self.tenant_home, self.role)

        self.superuser = User.objects.create_superuser(username="root", password="password")

    def test_non_member_cannot_bind_dashboard_to_foreign_tenant(self):
        # A user who is not a member of the foreign tenant POSTs its id directly.
        # The create form submits via HTMX, so mirror that with HTTP_HX_REQUEST.
        self.client.login(username="member", password="password")
        url = reverse("extras:dashboard_create")
        response = self.client.post(
            url,
            {
                "name": "Sneaky Board",
                "tenant": self.tenant_foreign.id,
            },
            HTTP_HX_REQUEST="true",
        )

        # Invalid/inaccessible targets use the same non-disclosing client error
        # as missing targets, and no dashboard is persisted.
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Selected tenant does not exist.", status_code=400)

    def test_non_member_rejected_on_plain_post_no_dashboard_bound(self):
        # Same boundary on a non-HTMX POST: a redirect (no HX header) but still
        # no dashboard bound to the foreign tenant.
        self.client.login(username="member", password="password")
        url = reverse("extras:dashboard_create")
        response = self.client.post(
            url,
            {
                "name": "Sneaky Board 2",
                "tenant": self.tenant_foreign.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Dashboard.objects.filter(tenant=self.tenant_foreign).exists())

    def test_member_can_create_dashboard_for_own_tenant(self):
        self.client.login(username="member", password="password")
        url = reverse("extras:dashboard_create")
        response = self.client.post(
            url,
            {
                "name": "Home Board",
                "tenant": self.tenant_home.id,
            },
        )

        self.assertRedirects(response, reverse("dashboard"))
        board = Dashboard.objects.filter(user=self.member, name="Home Board").first()
        self.assertIsNotNone(board)
        self.assertEqual(board.tenant, self.tenant_home)

    def test_superuser_can_bind_dashboard_to_any_tenant(self):
        # Superusers keep the global view: binding to a tenant they are not a
        # member of is allowed.
        self.client.login(username="root", password="password")
        url = reverse("extras:dashboard_create")
        response = self.client.post(
            url,
            {
                "name": "Admin Board",
                "tenant": self.tenant_foreign.id,
            },
        )

        self.assertRedirects(response, reverse("dashboard"))
        board = Dashboard.objects.filter(user=self.superuser, name="Admin Board").first()
        self.assertIsNotNone(board)
        self.assertEqual(board.tenant, self.tenant_foreign)


class DashboardManageModalScopingTests(TestCase):
    def setUp(self):
        self.tenant_home = Tenant.objects.create(name="Home Corp", slug="home-corp")
        self.tenant_foreign = Tenant.objects.create(name="Foreign Corp", slug="foreign-corp")

        self.role = Role.objects.create(tenant=self.tenant_home, name="Member", permissions=[])

        self.member = User.objects.create_user(username="member", password="password")
        grant(self.member, self.tenant_home, self.role)

        self.superuser = User.objects.create_superuser(username="root", password="password")

    def test_manage_modal_dropdown_scoped_to_memberships(self):
        self.client.login(username="member", password="password")
        response = self.client.get(reverse("extras:dashboard_manage_modal"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Member sees only their own tenant, never the foreign one.
        self.assertIn("Home Corp", body)
        self.assertNotIn("Foreign Corp", body)

    def test_manage_modal_dropdown_global_for_superuser(self):
        self.client.login(username="root", password="password")
        response = self.client.get(reverse("extras:dashboard_manage_modal"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Superuser keeps the global view across all tenants.
        self.assertIn("Home Corp", body)
        self.assertIn("Foreign Corp", body)


class DashboardManagedReachTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(name="MSP Provider", slug="msp-provider", is_provider=True)
        self.customer = Tenant.objects.create(
            name="Managed Customer",
            slug="managed-customer",
            managed_by=self.provider,
        )
        self.unrelated = Tenant.objects.create(name="Unrelated Customer", slug="unrelated-customer")
        self.deleted = Tenant.objects.create(
            name="Deleted Customer",
            slug="deleted-customer",
            managed_by=self.provider,
            deleted_at=timezone.now(),
        )
        self.user = User.objects.create_user(username="msp-admin", password="password", is_staff=True)
        self.role = Role.objects.create(tenant=self.provider, name="MSP Operator", permissions=[])
        self.role_grant = grant(
            self.user,
            self.provider,
            self.role,
            reach="managed",
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[self.customer],
        )
        self.provider_membership = self.role_grant.membership

    def test_inactive_user_has_no_dashboard_target_reach(self):
        inactive = User.objects.create_user(username="inactive-msp", password="password", is_active=False)
        grant(inactive, self.provider, self.role)

        self.assertEqual(list(dashboard_target_tenants(inactive)), [])

    def _activate_provider(self):
        self.client.login(username="msp-admin", password="password")
        session = self.client.session
        session["active_tenant_id"] = self.provider.pk
        session.save()

    def test_manage_modal_lists_canonical_managed_reach_only(self):
        self._activate_provider()

        response = self.client.get(reverse("extras:dashboard_manage_modal"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("MSP Provider", body)
        self.assertIn("Managed Customer", body)
        self.assertNotIn("Unrelated Customer", body)
        self.assertNotIn("Deleted Customer", body)
        self.assertFalse(Membership.objects.filter(user=self.user, tenant=self.customer).exists())

    def test_managed_only_target_can_create_dashboard(self):
        self._activate_provider()

        response = self.client.post(
            reverse("extras:dashboard_create"),
            {"name": "Managed Customer Board", "tenant": self.customer.pk},
        )

        self.assertRedirects(response, reverse("dashboard"))
        dashboard = Dashboard.objects.get(user=self.user, name="Managed Customer Board")
        self.assertEqual(dashboard.tenant_id, self.customer.pk)
        self.assertFalse(Membership.objects.filter(user=self.user, tenant=self.customer).exists())

    def test_invalid_dashboard_targets_share_non_disclosing_rejection(self):
        self._activate_provider()
        targets = {
            "unrelated": self.unrelated.pk,
            "deleted": self.deleted.pk,
            "nonexistent": 99999999,
            "malformed": "not-a-tenant",
        }

        for label, target in targets.items():
            with self.subTest(target=label):
                response = self.client.post(
                    reverse("extras:dashboard_create"),
                    {"name": f"Rejected {label}", "tenant": target},
                    HTTP_HX_REQUEST="true",
                )
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "Selected tenant does not exist.", status_code=400)

        self.assertFalse(Dashboard.objects.filter(user=self.user).exists())

    def test_superuser_sees_all_live_tenants_but_not_deleted(self):
        superuser = User.objects.create_superuser(username="msp-root", password="password")
        self.client.force_login(superuser)

        response = self.client.get(reverse("extras:dashboard_manage_modal"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for name in ("MSP Provider", "Managed Customer", "Unrelated Customer"):
            self.assertIn(name, body)
        self.assertNotIn("Deleted Customer", body)

    def test_direct_multi_membership_remains_selectable(self):
        second = Tenant.objects.create(name="Second Tenant", slug="second-tenant")
        second_role = Role.objects.create(tenant=second, name="Second Operator", permissions=[])
        grant(self.user, second, second_role)
        self._activate_provider()

        response = self.client.get(reverse("extras:dashboard_manage_modal"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("MSP Provider", body)
        self.assertIn("Managed Customer", body)
        self.assertIn("Second Tenant", body)
