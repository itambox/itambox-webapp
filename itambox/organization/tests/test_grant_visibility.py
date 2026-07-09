"""Regression tests for FIX #13 (§7 gap): surface effective grants for audit.

Since FIX #4 removed the JSON authoring UI for ``direct_permissions``, an admin
previously had no way to SEE what a membership grants. These tests assert:

  (a) The membership detail page renders the union of permission codenames from the
      membership's roles (role-derived) plus any codenames stored directly on the
      membership (``direct_permissions``) — read-only, not hidden.
  (b) The per-tenant "Who Has Access" audit page lists the actual permission
      codenames rather than only a bare count.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Membership, Role

User = get_user_model()


class MembershipDetailGrantVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Grant Corp", slug="grant-corp")
        self.admin = User.objects.create_superuser(
            username="grant_admin", email="grant_admin@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="grant_member", email="grant_member@x.com", password="pw",
        )
        # A role carrying a distinctive tenant-scoped permission codename.
        self.role = Role.objects.create(
            tenant=self.tenant, name="GrantViewer",
            permissions=["assets.view_asset"],
        )
        # Membership also carries a stored direct permission (legacy column data).
        self.membership = Membership.objects.create(
            user=self.member_user,
            tenant=self.tenant,
            is_active=True,
            direct_permissions=["assets.change_asset"],
        )
        self.membership.roles.add(self.role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_detail_page_shows_role_derived_and_direct_codenames(self):
        self.client_login_to_tenant(self.admin, self.tenant)
        url = reverse('organization:membership_detail', kwargs={'pk': self.membership.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Role-derived permission codename is surfaced (not hidden behind a count).
        self.assertIn("assets.view_asset", body)
        # Stored direct permission codename is surfaced too.
        self.assertIn("assets.change_asset", body)
        # The role name is shown as the source grouping.
        self.assertIn("GrantViewer", body)


class WhoHasAccessCodenameVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Access Corp", slug="access-corp")
        self.admin = User.objects.create_superuser(
            username="access_admin", email="access_admin@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="access_member", email="access_member@x.com", password="pw",
        )
        self.role = Role.objects.create(
            tenant=self.tenant, name="AccessViewer",
            permissions=["assets.view_asset"],
        )
        self.membership = Membership.objects.create(
            user=self.member_user,
            tenant=self.tenant,
            is_active=True,
            direct_permissions=["assets.change_asset"],
        )
        self.membership.roles.add(self.role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_who_has_access_lists_codenames_not_only_count(self):
        self.client_login_to_tenant(self.admin, self.tenant)
        url = reverse('organization:tenant_access', kwargs={'pk': self.tenant.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The audit surface now lists the actual effective codenames for the user,
        # drawn from both the tenant-scoped role and the stored direct permissions.
        self.assertIn("assets.view_asset", body)
        self.assertIn("assets.change_asset", body)
