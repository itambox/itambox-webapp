"""Regression tests for the RoleAssignment reach/scope helpers (post RBAC collapse).

Provider-staff tenant-scope resolution used to live on ``Membership``
(``covers_tenant`` / ``scoped_tenant_ids`` / ``project_permissions_for_tenant``), with a
"strip ``organization.manage_*`` on the provider->tenant projection" step. Per
``scratch/RBAC_STAGE2_SPEC.md`` (§1 Membership/RoleAssignment, §6 vocabulary rewiring):

  * The ``Provider`` model and the ``organization.manage_*`` capability vocabulary are
    DELETED — there is nothing left to strip. Role content alone decides what a grant
    conveys; the escalation guard (``core.auth.guards``) decides who may create it.
  * ``Membership`` is now a thin ``(user, tenant, is_active)`` anchor with no
    reachability logic of its own.
  * Reachability (``covers_tenant`` / ``scoped_tenant_ids``) now lives on
    :class:`organization.models.RoleAssignment` — the per-grant row — keyed off
    ``reach='managed'`` + ``managed_scope`` (``all`` / ``tenant_group`` / ``explicit``),
    with the provider identity being the assignment's own ``membership.tenant``.

This module ports the reachability coverage onto ``RoleAssignment`` (SCOPE_ALL,
SCOPE_TENANT_GROUP including the descendant walk and a cycle guard, SCOPE_EXPLICIT,
``reach='own'`` returning False/empty, and the cross-provider guard — a foreign
managing tenant's tenant sharing a group id must NOT be covered) and replaces the old
"stripping" test with the new invariant, verified end to end through
``MembershipBackend``/``has_perm``: a managed-reach role's FULL permission content
(including ``organization.*``) applies inside a tenant it covers, and never leaks into
a managed tenant the grant's scope does not cover.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.tests.mixins import grant
from core.auth import MembershipBackend
from core.managers import set_current_tenant, set_current_membership
from organization.models import Tenant, TenantGroup, Role, RoleAssignment
from organization.access import managed_accessible_tenant_ids

User = get_user_model()


class CoversTenantScopeTests(TestCase):
    """RoleAssignment.covers_tenant / scoped_tenant_ids across reach and scope."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.msp = Tenant.objects.create(name="Acme MSP", slug="acme-msp", is_provider=True)
        # A second, unrelated managing tenant to exercise the cross-provider negative case.
        self.other_msp = Tenant.objects.create(name="Rival MSP", slug="rival-msp", is_provider=True)

        # Group hierarchy: parent -> child -> grandchild (descendant-walk coverage).
        self.g_parent = TenantGroup.objects.create(name="Parent Grp", slug="parent-grp")
        self.g_child = TenantGroup.objects.create(name="Child Grp", slug="child-grp", parent=self.g_parent)
        self.g_grandchild = TenantGroup.objects.create(
            name="Grandchild Grp", slug="grandchild-grp", parent=self.g_child,
        )
        self.g_sibling = TenantGroup.objects.create(name="Sibling Grp", slug="sibling-grp")

        # Tenants managed by Acme.
        self.t_in_child = Tenant.objects.create(
            name="T Child", slug="t-child", managed_by=self.msp, group=self.g_child,
        )
        self.t_in_grandchild = Tenant.objects.create(
            name="T Grand", slug="t-grand", managed_by=self.msp, group=self.g_grandchild,
        )
        self.t_in_sibling = Tenant.objects.create(
            name="T Sibling", slug="t-sibling", managed_by=self.msp, group=self.g_sibling,
        )
        self.t_no_group = Tenant.objects.create(
            name="T Nogroup", slug="t-nogroup", managed_by=self.msp,
        )
        # Tenant managed by the OTHER msp, but placed inside Acme's group hierarchy on paper.
        self.t_other_msp = Tenant.objects.create(
            name="T Other", slug="t-other", managed_by=self.other_msp, group=self.g_child,
        )

        self.user = User.objects.create_user(username='tech1', email='tech1@example.com')
        self.role = Role.objects.create(
            tenant=self.msp, name="MSP Technician", permissions=['assets.view_asset'],
        )

    def _assignment(self, managed_scope, scope_group=None, assigned=None,
                     reach=RoleAssignment.REACH_MANAGED):
        return grant(
            self.user, self.msp, self.role, reach=reach,
            managed_scope=managed_scope, scope_group=scope_group,
            assigned_tenants=assigned,
        )

    # ------------------------------------------------------------------ SCOPE_ALL
    def test_scope_all_covers_every_managed_tenant(self):
        a = self._assignment(RoleAssignment.SCOPE_ALL)
        self.assertTrue(a.covers_tenant(self.t_in_child))
        self.assertTrue(a.covers_tenant(self.t_no_group))
        # But NOT a tenant managed by a different provider tenant.
        self.assertFalse(a.covers_tenant(self.t_other_msp))
        expected = {
            self.t_in_child.pk, self.t_in_grandchild.pk,
            self.t_in_sibling.pk, self.t_no_group.pk,
        }
        self.assertEqual(a.scoped_tenant_ids(), expected)

    # ------------------------------------------------------------ SCOPE_TENANT_GROUP
    def test_scope_group_walks_descendants(self):
        a = self._assignment(RoleAssignment.SCOPE_TENANT_GROUP, scope_group=self.g_parent)
        # The scope group itself + descendants are covered.
        self.assertTrue(a.covers_tenant(self.t_in_child))
        self.assertTrue(a.covers_tenant(self.t_in_grandchild))
        # A sibling group (not a descendant) is NOT covered.
        self.assertFalse(a.covers_tenant(self.t_in_sibling))
        # A tenant with no group is NOT covered under group scope.
        self.assertFalse(a.covers_tenant(self.t_no_group))
        self.assertEqual(
            a.scoped_tenant_ids(),
            {self.t_in_child.pk, self.t_in_grandchild.pk},
        )

    def test_scope_group_cross_provider_excluded(self):
        # A tenant sitting in Acme's group but MANAGED BY a different provider tenant
        # must not be covered, even though the group id matches on paper.
        a = self._assignment(RoleAssignment.SCOPE_TENANT_GROUP, scope_group=self.g_child)
        self.assertFalse(a.covers_tenant(self.t_other_msp))
        self.assertNotIn(self.t_other_msp.pk, a.scoped_tenant_ids())

    def test_scope_group_with_no_scope_group_covers_nothing(self):
        # tenant_group scope but scope_group left unset -> nothing is covered.
        a = self._assignment(RoleAssignment.SCOPE_TENANT_GROUP, scope_group=None)
        self.assertFalse(a.covers_tenant(self.t_in_child))
        self.assertFalse(a.covers_tenant(self.t_no_group))
        self.assertEqual(a.scoped_tenant_ids(), set())

    def test_scope_group_cycle_guarded(self):
        # A malformed cycle in TenantGroup.parent must not infinite-loop the descendant
        # walk that covers_tenant/scoped_tenant_ids delegate to
        # (organization.access.get_descendant_tenant_group_ids).
        g1 = TenantGroup.objects.create(name="Cycle A", slug="cycle-a")
        g2 = TenantGroup.objects.create(name="Cycle B", slug="cycle-b", parent=g1)
        # .update(): TenantGroup.clean() now rejects cycles on the save path,
        # so seed the malformed data behind validation's back.
        TenantGroup._base_manager.filter(pk=g1.pk).update(parent=g2)
        g1.refresh_from_db()

        t_in_cycle = Tenant.objects.create(
            name="T Cycle", slug="t-cycle", managed_by=self.msp, group=g2,
        )
        a = self._assignment(RoleAssignment.SCOPE_TENANT_GROUP, scope_group=g1)
        # Terminates (does not hang) and still correctly covers the tenant reachable
        # through the cyclic group graph.
        self.assertTrue(a.covers_tenant(t_in_cycle))
        self.assertEqual(a.scoped_tenant_ids(), {t_in_cycle.pk})

    # -------------------------------------------------------------- SCOPE_EXPLICIT
    def test_scope_explicit_only_assigned(self):
        a = self._assignment(RoleAssignment.SCOPE_EXPLICIT, assigned=[self.t_in_child])
        self.assertTrue(a.covers_tenant(self.t_in_child))
        self.assertFalse(a.covers_tenant(self.t_in_grandchild))
        self.assertFalse(a.covers_tenant(self.t_no_group))
        self.assertEqual(a.scoped_tenant_ids(), {self.t_in_child.pk})

    def test_default_scope_is_explicit(self):
        # A managed-reach assignment saved without managed_scope defaults to explicit
        # (via RoleAssignment.clean()).
        a = grant(self.user, self.msp, self.role, reach=RoleAssignment.REACH_MANAGED)
        self.assertEqual(a.managed_scope, RoleAssignment.SCOPE_EXPLICIT)
        self.assertFalse(a.covers_tenant(self.t_in_child))  # nothing assigned yet
        self.assertEqual(a.scoped_tenant_ids(), set())

    # -------------------------------------------------------------------- reach='own'
    def test_own_reach_covers_nothing(self):
        # An own-reach grant (even one sitting on a membership at an is_provider
        # tenant — e.g. the MSP admin's own local permissions) never covers any
        # tenant: reach='own' has no managed-tenant projection at all.
        a = grant(self.user, self.msp, self.role, reach=RoleAssignment.REACH_OWN)
        self.assertFalse(a.covers_tenant(self.t_in_child))
        self.assertFalse(a.covers_tenant(self.t_no_group))
        self.assertEqual(a.scoped_tenant_ids(), set())

    # -------------------------------------- managed_accessible_tenant_ids parity
    def test_access_helper_reuses_scoped_ids(self):
        # managed_accessible_tenant_ids must equal the union of scoped_tenant_ids
        # across the user's managed-reach assignments (it delegates to the same
        # canonical helper).
        self._assignment(RoleAssignment.SCOPE_TENANT_GROUP, scope_group=self.g_child)
        self.assertEqual(
            managed_accessible_tenant_ids(self.user),
            {self.t_in_child.pk, self.t_in_grandchild.pk},
        )


class ManagedReachPermParityTests(TestCase):
    """A managed-reach grant's effective tenant perms via ``has_perm`` — no stripping.

    Exercises the full projection path: a managed-reach role covering one specific
    managed tenant applies its FULL permission content there — including an ordinary
    ``organization.*`` permission, since the capability-strip vocabulary is deleted and
    role content alone decides what a grant conveys — and never leaks into a sibling
    managed tenant the grant's scope does not cover.
    """

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.msp = Tenant.objects.create(name="Parity MSP", slug="parity-msp", is_provider=True)
        self.tenant = Tenant.objects.create(
            name="Customer T", slug="customer-t", managed_by=self.msp,
        )
        self.tenant_outside = Tenant.objects.create(
            name="Unassigned T", slug="unassigned-t", managed_by=self.msp,
        )
        self.user = User.objects.create_user(username='msp-tech', email='msp-tech@example.com')

        # Managed-reach role carrying an ordinary tenant perm AND a standard
        # organization.* perm — both must project through unchanged.
        self.role = Role.objects.create(
            tenant=self.msp,
            name="MSP Technician",
            permissions=['assets.view_asset', 'organization.change_tenant'],
        )
        self.assignment = grant(
            self.user, self.msp, self.role, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, assigned_tenants=[self.tenant],
        )

    def test_effective_perms_projected_without_stripping(self):
        backend = MembershipBackend()
        perms = backend._effective_perms_for_tenant(self.user, self.tenant)
        # Ordinary tenant perm projects through.
        self.assertIn('assets.view_asset', perms)
        # organization.* is an ordinary permission now: role content decides, and
        # nothing is stripped by the managed projection.
        self.assertIn('organization.change_tenant', perms)

    def test_has_perm_grants_projected_tenant_perm(self):
        # End-to-end has_perm parity for a covered managed tenant — including the
        # organization.* permission that the old capability strip would have dropped.
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertTrue(self.user.has_perm('organization.change_tenant', obj=self.tenant))

    def test_has_perm_denied_for_uncovered_tenant(self):
        # The grant's explicit scope does not include tenant_outside -> no projected
        # perms there, even though both tenants share the same managing tenant.
        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.tenant_outside))
        self.assertFalse(self.user.has_perm('organization.change_tenant', obj=self.tenant_outside))
