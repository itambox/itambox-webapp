"""Regression tests for FIX #13 (§7 gap): surface effective grants for audit.

Since FIX #4 removed the JSON authoring UI for ``direct_permissions`` (and RBAC
Stage-2 deleted the ``direct_permissions`` column entirely — a one-off "Direct
grants" role + RoleAssignment is the successor shape, per
``scratch/RBAC_STAGE2_SPEC.md`` §9), an admin previously had no way to SEE what a
membership grants. These tests assert:

  (a) The membership detail page renders the union of permission codenames across
      all of the membership's own-reach RoleAssignment rows — read-only, not hidden.
  (b) The per-tenant "Who Has Access" audit page lists the actual permission
      codenames rather than only a bare count.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin, grant
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
        # A second, one-off role stands in for the deleted direct_permissions column.
        self.direct_role = Role.objects.create(
            tenant=self.tenant, name="Direct grants",
            permissions=["assets.change_asset"],
        )
        grant(self.member_user, self.tenant, self.role)
        self.membership = grant(self.member_user, self.tenant, self.direct_role).membership

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
        # The one-off "direct grants" role's codename is surfaced too.
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
        self.direct_role = Role.objects.create(
            tenant=self.tenant, name="Direct grants",
            permissions=["assets.change_asset"],
        )
        grant(self.member_user, self.tenant, self.role)
        self.membership = grant(self.member_user, self.tenant, self.direct_role).membership

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
        # drawn from both roles' own-reach RoleAssignment grants.
        self.assertIn("assets.view_asset", body)
        self.assertIn("assets.change_asset", body)
