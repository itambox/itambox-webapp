"""Coverage for the RBAC badge helpers after the Provider-collapse (stage 2).

``organization.templatetags.rbac_badges`` is the single source of markup for
three post-collapse concepts:

  * an assignment's **reach** (``RoleAssignment.reach``: this tenant vs.
    managed tenants) -- ``reach_badge``
  * whether a **membership** carries any managed-reach grant at all ("staff")
    -- ``membership_kind_badge`` (backed by ``Membership.is_staff_membership``)
  * whether a **role definition** is shared down to managed tenants --
    ``shared_role_badge``

There is no more role "scope" (``Role.SCOPE_CHOICES`` is gone -- a Role is
always tenant-owned) and no more membership "kind" field (``Membership``
carries no KIND_* constants -- staff-ness is derived from its assignments).
Those choice-label-consistency tests have no successor: the wording they
guarded no longer exists anywhere to drift. What replaces them here is
coverage that ``organization/tables.py`` (``RoleTable``/``MembershipTable``)
actually goes through these helpers and renders without crashing.
"""
from django.template import Context, Template
from django.test import TestCase
from django.utils.safestring import SafeString

from core.tests.mixins import TenantTestMixin
from organization.models import Membership, Role, RoleAssignment, Tenant
from organization.tables import MembershipTable, RoleTable
from organization.templatetags.rbac_badges import (
    membership_kind_badge,
    reach_badge,
    shared_role_badge,
)


class ReachBadgeTests(TestCase):
    """``reach_badge`` is the single source of markup for an assignment's
    reach: purple for managed reach, blue for own-tenant reach. Accepts
    either a ``RoleAssignment`` instance or a bare reach value string (some
    call sites only have the string, not a full row) -- no DB needed."""

    def test_own_reach_badge(self):
        html = reach_badge(RoleAssignment.REACH_OWN)
        self.assertIsInstance(html, SafeString)
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">This tenant</span>')

    def test_managed_reach_badge(self):
        html = reach_badge(RoleAssignment.REACH_MANAGED)
        self.assertEqual(str(html), '<span class="badge bg-purple-lt text-purple">Managed tenants</span>')

    def test_unrecognized_value_defaults_to_own(self):
        # Defensive default -- a bogus/blank value must never crash, and must
        # never silently render as the more-privileged-looking "managed" badge.
        html = reach_badge('bogus')
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">This tenant</span>')

    def test_accepts_a_roleassignment_instance(self):
        # Wording/markup must come from the same branch whether called with the
        # raw string or the row that carries it (unsaved instance -- no DB hit).
        assignment = RoleAssignment(reach=RoleAssignment.REACH_MANAGED)
        self.assertEqual(str(reach_badge(assignment)), str(reach_badge(RoleAssignment.REACH_MANAGED)))

    def test_icon_uses_the_right_icon_per_reach(self):
        self.assertEqual(
            str(reach_badge(RoleAssignment.REACH_OWN, icon=True)),
            '<span class="badge bg-blue-lt text-blue">'
            '<i class="mdi mdi-office-building me-1"></i>This tenant</span>',
        )
        self.assertEqual(
            str(reach_badge(RoleAssignment.REACH_MANAGED, icon=True)),
            '<span class="badge bg-purple-lt text-purple">'
            '<i class="mdi mdi-domain me-1"></i>Managed tenants</span>',
        )

    def test_icon_markup_is_not_double_escaped(self):
        # The icon fragment is built with format_html() and then nested inside
        # the outer format_html() call -- it must come through as literal
        # markup, not HTML-entity-escaped text.
        html = str(reach_badge(RoleAssignment.REACH_MANAGED, icon=True))
        self.assertIn('<i class="mdi mdi-domain me-1"></i>', html)
        self.assertNotIn('&lt;i', html)
        self.assertNotIn('&gt;', html)

    def test_extra_class_is_appended(self):
        html = reach_badge(RoleAssignment.REACH_MANAGED, icon=True, extra_class='align-middle ms-2')
        self.assertEqual(
            str(html),
            '<span class="badge bg-purple-lt text-purple align-middle ms-2">'
            '<i class="mdi mdi-domain me-1"></i>Managed tenants</span>',
        )

    def test_template_tag_matches_python_helper(self):
        tpl = Template('{% load rbac_badges %}{% reach_badge reach %}')
        rendered = tpl.render(Context({'reach': RoleAssignment.REACH_MANAGED}))
        self.assertEqual(rendered, str(reach_badge(RoleAssignment.REACH_MANAGED)))


class MembershipKindBadgeTests(TenantTestMixin, TestCase):
    """``membership_kind_badge`` shows purple "Staff" iff the membership
    carries at least one managed-reach ``RoleAssignment``
    (``Membership.is_staff_membership``); plain blue "Member" otherwise --
    including a membership with zero assignments. Needs real DB rows: the
    property queries ``self.assignments``."""

    def setUp(self):
        self.setup_tenant_context()
        self.msp_tenant = Tenant.objects.create(
            name='Northwind MSP', slug='northwind-msp', is_provider=True,
        )
        self.own_role = Role.objects.create(tenant=self.msp_tenant, name='Local Admin', permissions=[])
        self.managed_role = Role.objects.create(
            tenant=self.msp_tenant, name='MSP Technician', permissions=[], shared_with_managed=True,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_membership_with_no_assignments_is_a_member(self):
        membership = Membership.objects.create(user=self.tenant_user, tenant=self.msp_tenant)
        html = membership_kind_badge(membership)
        self.assertIsInstance(html, SafeString)
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">Member</span>')

    def test_membership_with_only_own_reach_is_a_member(self):
        assignment = self.grant(self.tenant_user, self.msp_tenant, self.own_role)
        html = membership_kind_badge(assignment.membership)
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">Member</span>')

    def test_membership_with_any_managed_reach_assignment_is_staff(self):
        self.grant(self.tenant_user, self.msp_tenant, self.own_role)
        assignment = self.grant(
            self.tenant_user, self.msp_tenant, self.managed_role,
            reach=RoleAssignment.REACH_MANAGED,
        )
        html = membership_kind_badge(assignment.membership)
        self.assertEqual(
            str(html), '<span class="badge bg-purple-lt text-purple">Staff</span>',
        )


class SharedRoleBadgeTests(TenantTestMixin, TestCase):
    """``shared_role_badge`` marks a role definition shared down to managed
    tenants; renders nothing (falsy, not a "No" badge) for an unshared role
    and for any object with no such attribute at all (defensive getattr)."""

    def setUp(self):
        self.setup_tenant_context()

    def tearDown(self):
        self.clear_tenant_context()

    def test_shared_role_shows_the_badge(self):
        role = Role.objects.create(
            tenant=self.tenant, name='MSP Technician', permissions=[], shared_with_managed=True,
        )
        html = shared_role_badge(role)
        self.assertIsInstance(html, SafeString)
        self.assertEqual(str(html), '<span class="badge bg-teal-lt text-teal">Shared</span>')

    def test_unshared_role_renders_nothing(self):
        role = Role.objects.create(tenant=self.tenant, name='Local Only', permissions=[])
        self.assertEqual(shared_role_badge(role), '')

    def test_object_without_the_attribute_renders_nothing(self):
        # Defensive getattr(..., False) -- must not blow up on a bare/partial
        # object that doesn't carry shared_with_managed at all.
        self.assertEqual(shared_role_badge(object()), '')


class TableRenderingTests(TenantTestMixin, TestCase):
    """``organization/tables.py`` (``RoleTable``/``MembershipTable``) renders
    its badge columns through the helpers above -- this is the guard against
    the tables and the helpers drifting apart again (e.g. a signature change
    in rbac_badges silently breaking a ``render_*`` method). Every declared
    column on both tables must render without raising."""

    def setUp(self):
        self.setup_tenant_context()
        self.msp_tenant = Tenant.objects.create(
            name='Northwind MSP', slug='northwind-msp', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Acme Customer', slug='acme-customer', managed_by=self.msp_tenant,
        )

    def tearDown(self):
        self.clear_tenant_context()

    # ------------------------------------------------------------------ RoleTable
    def test_role_table_shared_and_tenant_columns(self):
        shared_role = Role.objects.create(
            tenant=self.msp_tenant, name='MSP Technician', permissions=[], shared_with_managed=True,
        )
        local_role = Role.objects.create(tenant=self.msp_tenant, name='Local Admin', permissions=[])
        qs = Role.objects.filter(pk__in=[shared_role.pk, local_role.pk]).select_related('tenant')
        table = RoleTable(qs)
        rows_by_pk = {row.record.pk: row for row in table.rows}

        shared_cell = rows_by_pk[shared_role.pk].get_cell('shared')
        self.assertIn('Shared', shared_cell)
        self.assertIn('bg-teal-lt', shared_cell)

        unshared_cell = rows_by_pk[local_role.pk].get_cell('shared')
        self.assertEqual(unshared_cell, '—')

        tenant_cell = rows_by_pk[shared_role.pk].get_cell('tenant')
        self.assertIn(self.msp_tenant.get_absolute_url(), tenant_cell)
        self.assertIn('Northwind MSP', tenant_cell)

    def test_role_table_full_render_does_not_crash(self):
        role = Role.objects.create(
            tenant=self.msp_tenant, name='MSP Technician', permissions=[], shared_with_managed=True,
        )
        qs = RoleTable.Meta.model.objects.filter(pk=role.pk).select_related('tenant')
        table = RoleTable(qs)
        row = table.rows[0]
        for name in RoleTable.Meta.fields:
            row.get_cell(name)  # must not raise

    # ------------------------------------------------------------------ MembershipTable
    def test_membership_table_kind_badge_and_role_links(self):
        own_role = Role.objects.create(tenant=self.msp_tenant, name='Local Admin', permissions=[])
        managed_role = Role.objects.create(
            tenant=self.msp_tenant, name='MSP Technician', permissions=[], shared_with_managed=True,
        )

        member_assignment = self.grant(self.tenant_user, self.msp_tenant, own_role)
        member_membership = member_assignment.membership

        staff_user = self.tenant_admin
        self.grant(staff_user, self.msp_tenant, own_role)
        staff_assignment = self.grant(
            staff_user, self.msp_tenant, managed_role, reach=RoleAssignment.REACH_MANAGED,
        )
        staff_membership = staff_assignment.membership

        qs = Membership.objects.filter(
            pk__in=[member_membership.pk, staff_membership.pk],
        ).select_related('user', 'tenant')
        table = MembershipTable(qs)
        rows_by_pk = {row.record.pk: row for row in table.rows}

        member_kind_cell = rows_by_pk[member_membership.pk].get_cell('kind')
        self.assertIn('Member', member_kind_cell)
        self.assertIn('bg-blue-lt', member_kind_cell)

        staff_kind_cell = rows_by_pk[staff_membership.pk].get_cell('kind')
        self.assertIn('Staff', staff_kind_cell)
        self.assertIn('bg-purple-lt', staff_kind_cell)

        # An own-reach grant's link carries no reach badge; a managed-reach
        # grant's link does (RoleAssignment.reach == REACH_MANAGED check in
        # MembershipTable.render_roles).
        member_roles_cell = rows_by_pk[member_membership.pk].get_cell('roles')
        self.assertIn(own_role.name, member_roles_cell)
        self.assertNotIn('badge', member_roles_cell)

        staff_roles_cell = rows_by_pk[staff_membership.pk].get_cell('roles')
        self.assertIn(own_role.name, staff_roles_cell)
        self.assertIn(managed_role.name, staff_roles_cell)
        self.assertIn('bg-purple-lt', staff_roles_cell)

    def test_membership_table_renders_none_placeholder_with_no_assignments(self):
        membership = Membership.objects.create(user=self.tenant_admin, tenant=self.customer)
        qs = Membership.objects.filter(pk=membership.pk).select_related('user', 'tenant')
        table = MembershipTable(qs)
        cell = table.rows[0].get_cell('roles')
        self.assertIn('none', cell.lower())

    def test_membership_table_full_render_does_not_crash(self):
        role = Role.objects.create(tenant=self.msp_tenant, name='Local Admin', permissions=[])
        assignment = self.grant(self.tenant_user, self.msp_tenant, role)
        qs = MembershipTable.Meta.model.objects.filter(
            pk=assignment.membership.pk,
        ).select_related('user', 'tenant')
        table = MembershipTable(qs)
        row = table.rows[0]
        for name in MembershipTable.Meta.fields:
            row.get_cell(name)  # must not raise
