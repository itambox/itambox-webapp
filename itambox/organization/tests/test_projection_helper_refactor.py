"""Regression tests for the shared RBAC projection/scope helpers (FIX #14, §4).

The provider-staff tenant-scope resolution and the "strip ``organization.manage_*`` on the
provider->tenant projection" logic used to be hand-copied across ``core.auth``,
``organization.access``, and ``organization.signals``. They now live on the ``Membership``
model as the single source of truth:

  * :meth:`Membership.covers_tenant`
  * :meth:`Membership.scoped_tenant_ids`
  * :meth:`Membership.project_permissions_for_tenant`

These tests pin the behaviour of the canonical helpers and assert the refactor is
behaviour-preserving (a provider-staff user's effective tenant perms via ``has_perm`` are
unchanged), so a future re-implementation cannot silently diverge.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.tests.mixins import TenantTestMixin
from core.auth import MembershipBackend
from core.managers import set_current_tenant, set_current_membership
from organization.models import Provider, Tenant, TenantGroup, Membership, Role
from organization.access import provider_accessible_tenant_ids

User = get_user_model()


class ProjectPermissionsForTenantTests(TestCase):
    """The manage_* strip helper drops ONLY organization.manage_* and keeps the rest."""

    def test_strips_only_manage_capabilities(self):
        perms = [
            'assets.view_asset',
            'assets.change_asset',
            'organization.manage_staff',
            'organization.manage_tenants',
            'organization.view_tenant',        # NOT a manage_* capability -> kept
        ]
        result = Membership.project_permissions_for_tenant(perms)
        self.assertIn('assets.view_asset', result)
        self.assertIn('assets.change_asset', result)
        self.assertIn('organization.view_tenant', result)
        self.assertNotIn('organization.manage_staff', result)
        self.assertNotIn('organization.manage_tenants', result)

    def test_handles_none_and_empty(self):
        self.assertEqual(Membership.project_permissions_for_tenant(None), [])
        self.assertEqual(Membership.project_permissions_for_tenant([]), [])

    def test_keeps_everything_when_no_manage_perms(self):
        perms = ['assets.view_asset', 'inventory.view_component']
        self.assertEqual(
            set(Membership.project_permissions_for_tenant(perms)),
            set(perms),
        )


class CoversTenantScopeTests(TestCase):
    """covers_tenant / scoped_tenant_ids across explicit / tenant_group / all scopes."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Provider.objects.create(name="Acme MSP", slug="acme-msp")
        # A second, unrelated provider to exercise the cross-provider negative case.
        self.other_provider = Provider.objects.create(name="Rival MSP", slug="rival-msp")

        # Group hierarchy: parent -> child -> grandchild (descendant-walk coverage).
        self.g_parent = TenantGroup.objects.create(name="Parent Grp", slug="parent-grp")
        self.g_child = TenantGroup.objects.create(name="Child Grp", slug="child-grp", parent=self.g_parent)
        self.g_grandchild = TenantGroup.objects.create(
            name="Grandchild Grp", slug="grandchild-grp", parent=self.g_child,
        )
        self.g_sibling = TenantGroup.objects.create(name="Sibling Grp", slug="sibling-grp")

        # Tenants under Acme.
        self.t_in_child = Tenant.objects.create(
            name="T Child", slug="t-child", provider=self.provider, group=self.g_child,
        )
        self.t_in_grandchild = Tenant.objects.create(
            name="T Grand", slug="t-grand", provider=self.provider, group=self.g_grandchild,
        )
        self.t_in_sibling = Tenant.objects.create(
            name="T Sibling", slug="t-sibling", provider=self.provider, group=self.g_sibling,
        )
        self.t_no_group = Tenant.objects.create(
            name="T Nogroup", slug="t-nogroup", provider=self.provider,
        )
        # Tenant under the OTHER provider, but placed inside Acme's group hierarchy on paper.
        self.t_other_provider = Tenant.objects.create(
            name="T Other", slug="t-other", provider=self.other_provider, group=self.g_child,
        )

        self.user = User.objects.create_user(username='tech1', email='tech1@example.com')

    def _staff(self, scope, scope_group=None, assigned=None):
        m = Membership.objects.create(
            user=self.user, provider=self.provider,
            tenant_scope=scope, scope_group=scope_group,
        )
        if assigned:
            m.assigned_tenants.set(assigned)
        return m

    # ------------------------------------------------------------------ SCOPE_ALL
    def test_scope_all_covers_every_provider_tenant(self):
        m = self._staff(Membership.SCOPE_ALL)
        self.assertTrue(m.covers_tenant(self.t_in_child))
        self.assertTrue(m.covers_tenant(self.t_no_group))
        # But NOT a tenant belonging to a different provider.
        self.assertFalse(m.covers_tenant(self.t_other_provider))
        # scoped_tenant_ids is exactly Acme's tenants.
        expected = {
            self.t_in_child.pk, self.t_in_grandchild.pk,
            self.t_in_sibling.pk, self.t_no_group.pk,
        }
        self.assertEqual(m.scoped_tenant_ids(), expected)

    # ------------------------------------------------------------ SCOPE_TENANT_GROUP
    def test_scope_group_walks_descendants(self):
        m = self._staff(Membership.SCOPE_TENANT_GROUP, scope_group=self.g_parent)
        # The scope group itself + descendants are covered.
        self.assertTrue(m.covers_tenant(self.t_in_child))
        self.assertTrue(m.covers_tenant(self.t_in_grandchild))
        # A sibling group (not a descendant) is NOT covered.
        self.assertFalse(m.covers_tenant(self.t_in_sibling))
        # A tenant with no group is NOT covered under group scope.
        self.assertFalse(m.covers_tenant(self.t_no_group))
        # scoped_tenant_ids matches (descendant walk, provider-restricted).
        self.assertEqual(
            m.scoped_tenant_ids(),
            {self.t_in_child.pk, self.t_in_grandchild.pk},
        )

    def test_scope_group_cross_provider_excluded(self):
        # A tenant sitting in Acme's group but owned by a DIFFERENT provider is excluded.
        m = self._staff(Membership.SCOPE_TENANT_GROUP, scope_group=self.g_child)
        self.assertFalse(m.covers_tenant(self.t_other_provider))
        self.assertNotIn(self.t_other_provider.pk, m.scoped_tenant_ids())

    def test_scope_group_with_no_scope_group_covers_nothing(self):
        # tenant_group scope but scope_group left unset -> nothing is covered.
        m = self._staff(Membership.SCOPE_TENANT_GROUP, scope_group=None)
        self.assertFalse(m.covers_tenant(self.t_in_child))
        self.assertFalse(m.covers_tenant(self.t_no_group))
        self.assertEqual(m.scoped_tenant_ids(), set())

    # -------------------------------------------------------------- SCOPE_EXPLICIT
    def test_scope_explicit_only_assigned(self):
        m = self._staff(Membership.SCOPE_EXPLICIT, assigned=[self.t_in_child])
        self.assertTrue(m.covers_tenant(self.t_in_child))
        self.assertFalse(m.covers_tenant(self.t_in_grandchild))
        self.assertFalse(m.covers_tenant(self.t_no_group))
        self.assertEqual(m.scoped_tenant_ids(), {self.t_in_child.pk})

    def test_default_scope_is_explicit(self):
        # A provider membership saved without tenant_scope defaults to explicit (via save()).
        m = Membership.objects.create(user=self.user, provider=self.provider)
        self.assertEqual(m.tenant_scope, Membership.SCOPE_EXPLICIT)
        self.assertFalse(m.covers_tenant(self.t_in_child))  # nothing assigned yet
        self.assertEqual(m.scoped_tenant_ids(), set())

    # ---------------------------------------------------------- tenant memberships
    def test_tenant_membership_covers_nothing(self):
        # A plain tenant membership is never provider-staff -> covers_tenant is always False
        # and scoped_tenant_ids is empty (guards the provider_id branch).
        tenant_mem = Membership.objects.create(user=self.user, tenant=self.t_in_child)
        self.assertFalse(tenant_mem.covers_tenant(self.t_in_child))
        self.assertEqual(tenant_mem.scoped_tenant_ids(), set())

    # -------------------------------------- provider_accessible_tenant_ids parity
    def test_access_helper_reuses_scoped_ids(self):
        # provider_accessible_tenant_ids must equal the union of scoped_tenant_ids across
        # the user's provider memberships (it now delegates to the same helper).
        self._staff(Membership.SCOPE_TENANT_GROUP, scope_group=self.g_child)
        self.assertEqual(
            provider_accessible_tenant_ids(self.user),
            {self.t_in_child.pk, self.t_in_grandchild.pk},
        )


class ProviderStaffPermParityTests(TenantTestMixin, TestCase):
    """A provider-staff user's effective tenant perms (via has_perm) survive the refactor.

    Exercises the full projection path: provider-scoped role attached to a staff membership
    whose scope covers the tenant, with organization.manage_* stripped in-tenant.
    """

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Provider.objects.create(name="Parity MSP", slug="parity-msp")
        self.tenant = Tenant.objects.create(
            name="Customer T", slug="customer-t", provider=self.provider,
        )
        self.tenant_outside = Tenant.objects.create(
            name="Unassigned T", slug="unassigned-t", provider=self.provider,
        )
        self.user = User.objects.create_user(username='msp-tech', email='msp-tech@example.com')

        # Provider-scoped role carrying a tenant perm + a provider capability.
        self.provider_role = Role.objects.create(
            provider=self.provider,
            name="MSP Technician",
            permissions=['assets.view_asset', 'organization.manage_staff'],
        )
        # Explicit scope, assigned ONLY to self.tenant.
        self.staff = Membership.objects.create(
            user=self.user, provider=self.provider, tenant_scope=Membership.SCOPE_EXPLICIT,
        )
        self.staff.assigned_tenants.set([self.tenant])
        self.staff.roles.add(self.provider_role)

    def test_effective_perms_projected_with_manage_stripped(self):
        backend = MembershipBackend()
        perms = backend._effective_perms_for_tenant(self.user, self.tenant)
        # Ordinary tenant perm projects through.
        self.assertIn('assets.view_asset', perms)
        # Provider capability is stripped in tenant context.
        self.assertNotIn('organization.manage_staff', perms)

    def test_has_perm_grants_projected_tenant_perm(self):
        # End-to-end has_perm parity for a covered tenant.
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.tenant))
        # manage_* never grants inside the tenant.
        self.assertFalse(self.user.has_perm('organization.manage_staff', obj=self.tenant))

    def test_has_perm_denied_for_uncovered_tenant(self):
        # The staff membership does NOT cover tenant_outside -> no projected perms there.
        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.tenant_outside))
