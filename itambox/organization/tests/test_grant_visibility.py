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

RBAC stage 3 adds two more visibility surfaces covered here:

  (c) The members list's lazy "Access from outside this tenant" panel
      (``?panel=outside_access`` on the membership list URL): external reach only
      (managed-reach staff + user-group members without a local membership row),
      read-only, empty response when nobody reaches the tenant from outside.
  (d) The members list renders in a constant number of queries regardless of row
      count (Staff/Member badge via an Exists() annotation, roles via prefetch).
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin, grant
from organization.access import tenant_access_report
from organization.models import Tenant, Membership, Role, RoleAssignment
from users.models import UserGroup

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


class OutsideAccessPanelTests(TenantTestMixin, TestCase):
    """The members list's "Access from outside this tenant" panel (stage 3, item 2):
    lazily fetched via ``?panel=outside_access`` on the membership list URL, sourced
    from ``tenant_access_report(..., external_only=True)`` — provider staff with
    managed reach and user-group members, minus anyone holding a local membership
    row. Read-only; empty body when nobody reaches the tenant from outside."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(
            name="Panel MSP", slug="panel-msp", is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name="Panel Customer", slug="panel-customer", managed_by=self.provider,
        )
        self.admin = User.objects.create_superuser(
            username="panel_admin", email="panel_admin@x.com", password="pw",
        )

        # Provider staff reaching the customer via a managed-reach grant.
        self.staff_user = User.objects.create_user(
            username="panel_staff", email="panel_staff@x.com", password="pw",
        )
        self.staff_role = Role.objects.create(
            tenant=self.provider, name="Panel Tech", permissions=["assets.view_asset"],
        )
        grant(
            self.staff_user, self.provider, self.staff_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_ALL,
        )

        # Group-based access: a user group carrying a role OWNED by the customer.
        self.group_user = User.objects.create_user(
            username="panel_groupie", email="panel_groupie@x.com", password="pw",
        )
        self.customer_role = Role.objects.create(
            tenant=self.customer, name="Panel Auditor",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        self.group = UserGroup.objects.create(name="Panel Auditors", tenant=self.provider)
        self.group.roles.add(self.customer_role)
        self.group.members.add(self.group_user)

        # A plain local member — must never appear in the outside-access panel.
        self.local_user = User.objects.create_user(
            username="panel_local", email="panel_local@x.com", password="pw",
        )
        self.local_role = Role.objects.create(
            tenant=self.customer, name="Panel Member", permissions=[],
        )
        grant(self.local_user, self.customer, self.local_role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _panel_url(self):
        return reverse('organization:membership_list') + '?panel=outside_access'

    def test_report_external_only_returns_outsiders_with_sources_and_groups(self):
        report = tenant_access_report(self.customer, external_only=True)
        by_user = {entry['user'].username: entry for entry in report}
        self.assertEqual(set(by_user), {"panel_staff", "panel_groupie"})
        self.assertEqual(by_user['panel_staff']['sources'], ['managed'])
        self.assertEqual(by_user['panel_staff']['permissions'], ['assets.view_asset'])
        self.assertEqual(by_user['panel_groupie']['sources'], ['group'])
        self.assertEqual(by_user['panel_groupie']['groups'], ['Panel Auditors'])
        self.assertEqual(
            by_user['panel_groupie']['permissions'],
            ['assets.change_asset', 'assets.view_asset'],
        )

    def test_report_default_shape_still_includes_local_members(self):
        # Back-compat for the "Who Has Access" page: without external_only the
        # report keeps listing local members (now with the extra 'groups' key).
        report = tenant_access_report(self.customer)
        by_user = {entry['user'].username: entry for entry in report}
        self.assertIn("panel_local", by_user)
        self.assertEqual(by_user['panel_local']['sources'], ['membership'])
        self.assertEqual(by_user['panel_local']['groups'], [])

    def test_panel_lists_external_users_with_provenance(self):
        self.client_login_to_tenant(self.admin, self.customer)
        response = self.client.get(self._panel_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Access from outside this tenant", body)
        # Managed-reach staff, labelled with the managing provider.
        self.assertIn("panel_staff", body)
        self.assertIn("Managed by", body)
        self.assertIn("Panel MSP", body)
        # Group member, labelled with the group.
        self.assertIn("panel_groupie", body)
        self.assertIn("Via group", body)
        self.assertIn("Panel Auditors", body)
        # Local members belong on the members list itself, not here.
        self.assertNotIn("panel_local", body)

    def test_panel_is_read_only(self):
        self.client_login_to_tenant(self.admin, self.customer)
        body = self.client.get(self._panel_url()).content.decode()
        # No grant-management actions: grants are managed where they live.
        self.assertNotIn("btn-action", body)
        self.assertNotIn("/edit/", body)
        self.assertNotIn("/delete/", body)

    def test_panel_excludes_external_user_with_any_local_membership_row(self):
        # Even a suspended local membership means the person is managed on the
        # members list itself — the outside-access panel must drop them.
        Membership.objects.create(
            user=self.staff_user, tenant=self.customer, is_active=False,
        )
        self.client_login_to_tenant(self.admin, self.customer)
        body = self.client.get(self._panel_url()).content.decode()
        self.assertNotIn("panel_staff", body)
        self.assertIn("panel_groupie", body)

    def test_panel_empty_when_no_external_access(self):
        lonely = Tenant.objects.create(name="Lonely Corp", slug="lonely-corp")
        self.client_login_to_tenant(self.admin, lonely)
        response = self.client.get(self._panel_url())
        self.assertEqual(response.status_code, 200)
        # Empty body — the members list shows no panel chrome at all.
        self.assertEqual(response.content.strip(), b"")

    def test_list_page_embeds_lazy_panel_container(self):
        self.client_login_to_tenant(self.admin, self.customer)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("panel=outside_access", body)
        self.assertIn('hx-trigger="load once"', body)

    def test_customer_admin_with_view_membership_sees_panel(self):
        viewer = User.objects.create_user(
            username="panel_viewer", email="panel_viewer@x.com", password="pw",
        )
        self.client_login_to_tenant(
            viewer, self.customer, role_permissions=['organization.view_membership'],
        )
        response = self.client.get(self._panel_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("panel_staff", body)
        self.assertIn("panel_groupie", body)


class MembershipListQueryCountTests(TenantTestMixin, TestCase):
    """N+1 guard for the members list: the Staff/Member badge is fed by an
    ``Exists()`` annotation and the roles column by a prefetch, so the total
    query count must not grow with the number of rendered rows."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(
            name="Count MSP", slug="count-msp", is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name="Count Customer", slug="count-customer", managed_by=self.provider,
        )
        self.admin = User.objects.create_superuser(
            username="count_admin", email="count_admin@x.com", password="pw",
        )
        self.role = Role.objects.create(
            tenant=self.provider, name="Count Role",
            permissions=["assets.view_asset"], shared_with_managed=True,
        )
        self._make_members(3)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _make_members(self, count):
        offset = User.objects.count()
        for i in range(count):
            user = User.objects.create_user(
                username=f"count_user_{offset}_{i}",
                email=f"count_user_{offset}_{i}@x.com",
                password="pw",
            )
            grant(user, self.provider, self.role)
            if i % 2 == 0:
                # Every other member also carries managed reach, so both badge
                # branches (Staff and Member) render in the measured requests.
                grant(
                    user, self.provider, self.role,
                    reach=RoleAssignment.REACH_MANAGED,
                    managed_scope=RoleAssignment.SCOPE_ALL,
                )

    def test_members_list_query_count_is_constant_in_row_count(self):
        self.client_login_to_tenant(self.admin, self.provider)
        url = reverse('organization:membership_list')

        # Warm per-process caches (ContentType, template loaders, session).
        warmup = self.client.get(url)
        self.assertEqual(warmup.status_code, 200)

        with CaptureQueriesContext(connection) as baseline:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # More than doubling the rendered rows must not add a single query —
        # any per-row exists()/role lookup would fail this immediately.
        self._make_members(7)
        with self.assertNumQueries(len(baseline.captured_queries)):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
