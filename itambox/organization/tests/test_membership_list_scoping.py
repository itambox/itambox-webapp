"""WS7 regression suite — Members list scoping matches the outside-access panel.

``MembershipListView`` used to list memberships from EVERY accessible tenant while
the outside-access panel was computed for the active tenant only, so multi-tenant
staff saw an unlabelled mixed table beside a tenant-specific audit. The list now
tracks the active context: a single active tenant lists only that tenant's local
memberships (no Tenant column) and shows its outside-access panel; a group scope
or superuser-global context lists the context's tenants WITH a Tenant column and
no single panel. See ``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §7.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import (
    set_current_tenant, set_current_tenant_group, set_current_membership,
)
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


class MembershipListScopingTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        self.group = TenantGroup.objects.create(name='WS7 Region', slug='ws7-region')
        self.provider = Tenant.objects.create(name='WS7 P', slug='ws7-p', is_provider=True)
        self.cust_a = Tenant.objects.create(
            name='WS7 A', slug='ws7-a', managed_by=self.provider, group=self.group,
        )
        self.cust_b = Tenant.objects.create(
            name='WS7 B', slug='ws7-b', managed_by=self.provider, group=self.group,
        )

        # Provider staff: local view_membership in P + managed-reach view_membership
        # over all managed tenants (so they CAN view A/B memberships, but must still
        # see only P's rows when P is the single active tenant).
        self.staff = User.objects.create_user(username='ws7_staff', password='pw')
        view_role = Role.objects.create(
            tenant=self.provider, name='Viewer', permissions=['organization.view_membership'],
            shared_with_managed=True,
        )
        grant(self.staff, self.provider, view_role)
        grant(
            self.staff, self.provider, view_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
        )

        # Local members in each tenant.
        self.p_member = self._member('ws7_pm', self.provider)
        self.a_member = self._member('ws7_am', self.cust_a)
        self.b_member = self._member('ws7_bm', self.cust_b)

        self.superuser = User.objects.create_superuser(
            username='ws7_su', email='ws7_su@x.com', password='pw',
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)

    def _member(self, username, tenant):
        user = User.objects.create_user(username=username, password='pw')
        role = Role.objects.create(tenant=tenant, name=f'{username} role', permissions=[])
        return grant(user, tenant, role).membership

    def _login_tenant(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.pop('active_tenant_group_id', None)
        session.save()

    def _login_group(self, user, group):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_group_id'] = group.pk
        session.pop('active_tenant_id', None)
        session.save()

    def _listed_pks(self, response):
        return {m.pk for m in response.context['object_list']}

    def test_single_tenant_lists_only_local_memberships(self):
        self._login_tenant(self.staff, self.provider)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = self._listed_pks(response)
        self.assertIn(self.p_member.pk, pks)
        self.assertNotIn(self.a_member.pk, pks)  # managed reach does NOT pull A's rows in
        self.assertNotIn(self.b_member.pk, pks)
        # No Tenant column under a single active tenant.
        self.assertNotIn('tenant', response.context['table'].columns.names())

    def test_single_tenant_shows_its_outside_access_panel(self):
        # Switch to customer A: staff reach into A shows in A's outside-access panel.
        self._login_tenant(self.staff, self.cust_a)
        response = self.client.get(reverse('organization:membership_list'))
        pks = self._listed_pks(response)
        self.assertIn(self.a_member.pk, pks)
        self.assertNotIn(self.p_member.pk, pks)
        self.assertEqual(response.context['outside_access_tenant'], self.cust_a)

    def test_group_scope_shows_tenant_column_and_no_panel(self):
        self._login_group(self.staff, self.group)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = self._listed_pks(response)
        # Memberships from the accessible tenants inside the group (A and B).
        self.assertIn(self.a_member.pk, pks)
        self.assertIn(self.b_member.pk, pks)
        self.assertNotIn(self.p_member.pk, pks)  # P is not in the group
        self.assertIn('tenant', response.context['table'].columns.names())
        self.assertIsNone(response.context['outside_access_tenant'])

    def test_superuser_global_shows_tenant_column_and_no_panel(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = self._listed_pks(response)
        self.assertTrue({self.p_member.pk, self.a_member.pk, self.b_member.pk} <= pks)
        self.assertIn('tenant', response.context['table'].columns.names())
        self.assertIsNone(response.context['outside_access_tenant'])

    def test_list_query_count_does_not_grow_with_rows(self):
        # The list query count must be independent of the row/assignment count (no
        # N+1), so a large tenant's members page stays constant-cost. New members
        # reuse a shared role (a per-member role would instead grow the filter
        # dropdown's choice count, a separate axis).
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        shared = Role.objects.create(tenant=self.provider, name='Shared Row Role', permissions=[])

        def add_member(username):
            user = User.objects.create_user(username=username, password='pw')
            m = grant(user, self.provider, shared).membership
            # A second assignment per membership to exercise the assignment prefetch.
            grant(user, self.provider, shared, reach=RoleAssignment.REACH_MANAGED,
                  managed_scope=RoleAssignment.SCOPE_ALL)
            return m

        for i in range(2):
            add_member(f'ws7_base_{i}')

        self._login_tenant(self.staff, self.provider)
        url = reverse('organization:membership_list')
        self.client.get(url)  # warm caches (content types, session)

        with CaptureQueriesContext(connection) as small:
            self.client.get(url)

        for i in range(6):
            add_member(f'ws7_extra_{i}')

        with CaptureQueriesContext(connection) as large:
            self.client.get(url)

        self.assertEqual(len(large), len(small))
