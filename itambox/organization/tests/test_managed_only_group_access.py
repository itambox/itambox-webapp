"""RC-blocker regression — a genuinely managed-only user under a tenant-group scope.

A managed-only technician holds ONE membership (at the provider) whose only
grants are managed-reach RoleAssignments into customer tenants. Under an active
tenant-group scope the ambient ``has_perm(perm, obj=None)`` gate used to anchor
at the first membership's tenant — the provider, where such a user holds no
own-reach roles — and 403'd every generic list page before the correctly
group-scoped queryset ever ran. The first-membership fallback also stomped the
group context mid-request (``set_current_tenant(provider)`` under an active
group scope). The gate now evaluates the accessible tenants inside the scoped
subtree (pass on ANY match, fail closed on none), mirroring
``TenantScopingQuerySet.filter_by_tenant``.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.managers import (
    get_current_membership, get_current_tenant, get_current_tenant_group,
    set_current_tenant, set_current_tenant_group, set_current_membership,
)
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


class ManagedOnlyFixtureMixin:
    """Provider P; customers A + B in group G; customer C in group G2 (managed by
    P but OUTSIDE the technician's grant); a managed-only technician whose single
    membership at P carries one managed-reach view role covering exactly A + B."""

    def _build_fixture(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        self.group = TenantGroup.objects.create(name='MO Region', slug='mo-region')
        self.other_group = TenantGroup.objects.create(name='MO Other', slug='mo-other')
        self.provider = Tenant.objects.create(name='MO P', slug='mo-p', is_provider=True)
        self.cust_a = Tenant.objects.create(
            name='MO A', slug='mo-a', managed_by=self.provider, group=self.group,
        )
        self.cust_b = Tenant.objects.create(
            name='MO B', slug='mo-b', managed_by=self.provider, group=self.group,
        )
        self.cust_c = Tenant.objects.create(
            name='MO C', slug='mo-c', managed_by=self.provider, group=self.other_group,
        )

        # Managed-only: NO own-reach assignment anywhere; one managed-reach view
        # role covering exactly A and B.
        self.tech = User.objects.create_user(username='mo_tech', password='pw')
        self.view_role = Role.objects.create(
            tenant=self.provider, name='MO Viewer', shared_with_managed=True,
            permissions=['organization.view_membership', 'assets.view_asset'],
        )
        self.tech_grant = grant(
            self.tech, self.provider, self.view_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_a, self.cust_b],
        )

        # Local members in each tenant for row-scoping assertions.
        self.p_member = self._local_member('mo_pm', self.provider)
        self.a_member = self._local_member('mo_am', self.cust_a)
        self.b_member = self._local_member('mo_bm', self.cust_b)

    def _local_member(self, username, tenant):
        user = User.objects.create_user(username=username, password='pw')
        role = Role.objects.create(tenant=tenant, name=f'{username} role', permissions=[])
        return grant(user, tenant, role).membership

    def _clear_context(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)


class ManagedOnlyGroupGateBackendTests(ManagedOnlyFixtureMixin, TestCase):
    """The ambient permission gate itself, exercised via contextvars directly."""

    def setUp(self):
        self._build_fixture()

    def tearDown(self):
        self._clear_context()

    def _fresh_tech(self):
        # A fresh instance so per-user permission caches never bleed between checks.
        return User.objects.get(pk=self.tech.pk)

    def test_ambient_gate_passes_under_group_scope(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertTrue(self._fresh_tech().has_perm('organization.view_membership'))
        self.assertTrue(self._fresh_tech().has_perm('assets.view_asset'))

    def test_ambient_gate_fails_closed_outside_the_grant(self):
        # G2 contains only customer C, which the grant does not cover: the user
        # reaches NOTHING in the scoped subtree, so the gate must refuse — never
        # fall back to some tenant outside the scope.
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.other_group)
        self.assertFalse(self._fresh_tech().has_perm('organization.view_membership'))

    def test_ambient_gate_denies_perms_the_role_does_not_carry(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertFalse(self._fresh_tech().has_perm('organization.delete_membership'))

    def test_single_tenant_context_is_unchanged(self):
        # Active tenant = the provider (their home): no own-reach roles there, so
        # the gate still refuses — group semantics never leak into single-tenant scope.
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_tenant(self.provider)
        self.assertFalse(self._fresh_tech().has_perm('organization.view_membership'))
        # Active tenant = a covered customer: managed projection applies as before.
        set_current_tenant(self.cust_a)
        self.assertTrue(self._fresh_tech().has_perm('organization.view_membership'))

    def test_group_scope_check_leaves_the_context_untouched(self):
        # The old first-membership fallback called set_current_tenant(provider)
        # mid-check, silently converting the group scope into a single-tenant
        # provider scope for the rest of the request.
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self._fresh_tech().has_perm('organization.view_membership')
        self.assertIsNone(get_current_tenant())
        self.assertIsNone(get_current_membership())
        self.assertEqual(get_current_tenant_group(), self.group)

    def test_has_module_perms_under_group_scope(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertTrue(self._fresh_tech().has_module_perms('organization'))
        self.assertTrue(self._fresh_tech().has_module_perms('assets'))
        self.assertFalse(self._fresh_tech().has_module_perms('licenses'))

    def test_object_anchored_checks_ignore_the_group_scope(self):
        # An obj-carrying check stays anchored at the object's tenant even while
        # a group scope is active: C is inside no scope the grant covers.
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertTrue(
            self._fresh_tech().has_perm('organization.view_membership', obj=self.cust_a)
        )
        self.assertFalse(
            self._fresh_tech().has_perm('organization.view_membership', obj=self.cust_c)
        )

    def test_any_match_passes_when_the_perm_is_held_in_only_one_group_tenant(self):
        # Discriminates union semantics from an intersection regression: the
        # extra perm exists at A but NOT at B, both accessible in the scope.
        only_a_role = Role.objects.create(
            tenant=self.provider, name='MO Only-A', shared_with_managed=True,
            permissions=['licenses.view_license'],
        )
        grant(
            self.tech, self.provider, only_a_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_a],
        )
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertTrue(self._fresh_tech().has_perm('licenses.view_license'))
        self.assertTrue(self._fresh_tech().has_module_perms('licenses'))

    def test_bound_membership_does_not_anchor_the_group_gate(self):
        # A member of A (role-less) whose only view perm arrives via managed
        # reach into B: the middleware binds their A-membership under the group
        # scope, but the gate must evaluate the UNION, not the anchor tenant.
        anchor_user = User.objects.create_user(username='mo_anchor', password='pw')
        blank_role = Role.objects.create(tenant=self.cust_a, name='MO Anchor Blank', permissions=[])
        anchor_membership = grant(anchor_user, self.cust_a, blank_role).membership
        b_role = Role.objects.create(
            tenant=self.provider, name='MO Anchor B-View', shared_with_managed=True,
            permissions=['organization.view_membership'],
        )
        grant(
            anchor_user, self.provider, b_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_b],
        )
        set_current_tenant(None)
        set_current_membership(anchor_membership)
        set_current_tenant_group(self.group)
        fresh = User.objects.get(pk=anchor_user.pk)
        self.assertTrue(fresh.has_perm('organization.view_membership'))

    def test_scope_all_and_tenant_group_reach_pass_under_group_scope(self):
        # The SCOPE_EXPLICIT fixture never exercises the other two reach
        # branches; a scoped-manager fail-closed regression inside either
        # (a recurring bug class here) must not silently 403 these users.
        for username, scope_kwargs in (
            ('mo_all', {'managed_scope': RoleAssignment.SCOPE_ALL}),
            ('mo_grp', {'managed_scope': RoleAssignment.SCOPE_TENANT_GROUP,
                        'scope_group': self.group}),
        ):
            with self.subTest(username=username):
                user = User.objects.create_user(username=username, password='pw')
                grant(
                    user, self.provider, self.view_role,
                    reach=RoleAssignment.REACH_MANAGED, **scope_kwargs,
                )
                set_current_tenant(None)
                set_current_membership(None)
                set_current_tenant_group(self.group)
                fresh = User.objects.get(pk=user.pk)
                self.assertTrue(fresh.has_perm('organization.view_membership'))

    def test_nested_child_group_tenant_satisfies_the_parent_scope(self):
        child = TenantGroup.objects.create(
            name='MO Child', slug='mo-child', parent=self.group,
        )
        cust_d = Tenant.objects.create(
            name='MO D', slug='mo-d', managed_by=self.provider, group=child,
        )
        nested_tech = User.objects.create_user(username='mo_nested', password='pw')
        grant(
            nested_tech, self.provider, self.view_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[cust_d],
        )
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)  # parent scope, tenant sits in the child
        fresh = User.objects.get(pk=nested_tech.pk)
        self.assertTrue(fresh.has_perm('organization.view_membership'))

    def test_soft_deleted_subgroup_tenants_do_not_satisfy_the_gate(self):
        # The gate's descendant walk prunes at soft-deleted nodes exactly like
        # filter_by_tenant: a perm held ONLY in a dead subgroup's tenant must
        # not open pages that will never show that tenant's rows.
        dead = TenantGroup.objects.create(name='MO Dead', slug='mo-dead', parent=self.group)
        cust_e = Tenant.objects.create(
            name='MO E', slug='mo-e', managed_by=self.provider, group=dead,
        )
        TenantGroup._base_manager.filter(pk=dead.pk).update(deleted_at=timezone.now())
        sw_role = Role.objects.create(
            tenant=self.provider, name='MO Software', shared_with_managed=True,
            permissions=['software.view_installedsoftware'],
        )
        grant(
            self.tech, self.provider, sw_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[cust_e],
        )
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertFalse(self._fresh_tech().has_perm('software.view_installedsoftware'))

    def test_tenant_less_object_follows_the_group_union(self):
        # A tenant-less (global/shared) object cannot anchor a tenant, so its
        # check follows the same group-union semantics as the ambient gate.
        global_obj = SimpleNamespace(tenant=None)
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(self.group)
        self.assertTrue(
            self._fresh_tech().has_perm('organization.view_membership', obj=global_obj)
        )
        set_current_tenant_group(self.other_group)
        self.assertFalse(
            self._fresh_tech().has_perm('organization.view_membership', obj=global_obj)
        )


class ManagedOnlyGroupHttpTests(ManagedOnlyFixtureMixin, TestCase):
    """End-to-end HTTP: generic list pages for a genuinely managed-only user."""

    def setUp(self):
        self._build_fixture()

    def tearDown(self):
        self._clear_context()

    def _login_group(self, user, group):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_group_id'] = group.pk
        session.pop('active_tenant_id', None)
        session.save()

    def _login_tenant(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.pop('active_tenant_group_id', None)
        session.save()

    def test_membership_list_is_200_and_scoped_under_group_scope(self):
        self._login_group(self.tech, self.group)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = {m.pk for m in response.context['object_list']}
        self.assertIn(self.a_member.pk, pks)
        self.assertIn(self.b_member.pk, pks)
        # The provider is outside the group: its rows must not appear, and
        # neither may the technician's own provider membership.
        self.assertNotIn(self.p_member.pk, pks)
        self.assertNotIn(
            Membership.objects.get(user=self.tech, tenant=self.provider).pk, pks,
        )

    def test_asset_list_is_200_under_group_scope(self):
        self._login_group(self.tech, self.group)
        response = self.client.get(reverse('assets:asset_list'))
        self.assertEqual(response.status_code, 200)

    def test_covered_customer_single_tenant_still_works(self):
        self._login_tenant(self.tech, self.cust_a)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = {m.pk for m in response.context['object_list']}
        self.assertIn(self.a_member.pk, pks)
        self.assertNotIn(self.b_member.pk, pks)

    def test_provider_home_tenant_still_403s(self):
        # Single-tenant scope at the provider: the technician holds no own-reach
        # roles there, so the page is (and stays) forbidden.
        self._login_tenant(self.tech, self.provider)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 403)

    def test_group_scope_403s_when_the_role_grants_no_view_perm(self):
        # Reach without permission content: the empty role makes A accessible
        # (it appears in the switcher / queryset scope) but conveys no view
        # permission, so the gate must still refuse.
        blank_tech = User.objects.create_user(username='mo_blank', password='pw')
        blank_role = Role.objects.create(
            tenant=self.provider, name='MO Blank', shared_with_managed=True,
            permissions=[],
        )
        grant(
            blank_tech, self.provider, blank_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_a],
        )
        self._login_group(blank_tech, self.group)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 403)

    def test_scope_all_reach_serves_the_group_list_page(self):
        all_tech = User.objects.create_user(username='mo_all_http', password='pw')
        grant(
            all_tech, self.provider, self.view_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
        )
        self._login_group(all_tech, self.group)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = {m.pk for m in response.context['object_list']}
        self.assertIn(self.a_member.pk, pks)
        self.assertIn(self.b_member.pk, pks)

    def test_nested_child_group_activates_the_parent_scope(self):
        # The technician's only covered tenant sits in a CHILD group: activating
        # the parent must still resolve the scope (middleware walks descendants,
        # matching filter_by_tenant and the gate) instead of silently refusing
        # it and 403ing at the provider-home fallback.
        child = TenantGroup.objects.create(
            name='MO HTTP Child', slug='mo-http-child', parent=self.group,
        )
        cust_d = Tenant.objects.create(
            name='MO HTTP D', slug='mo-http-d', managed_by=self.provider, group=child,
        )
        d_member = self._local_member('mo_dm', cust_d)
        nested_tech = User.objects.create_user(username='mo_nested_http', password='pw')
        grant(
            nested_tech, self.provider, self.view_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[cust_d],
        )
        self._login_group(nested_tech, self.group)
        response = self.client.get(reverse('organization:membership_list'))
        self.assertEqual(response.status_code, 200)
        pks = {m.pk for m in response.context['object_list']}
        self.assertIn(d_member.pk, pks)

    def test_bulk_delete_skips_rows_in_tenants_without_the_delete_perm(self):
        # Heterogeneous per-tenant perms: delete rights in A, view-only in B.
        # The ambient gate passes (delete held SOMEWHERE in the scope), but the
        # per-row check must confine the mutation to A.
        from assets.models import Asset, AssetType, StatusLabel, Manufacturer

        rw_role = Role.objects.create(
            tenant=self.provider, name='MO Asset RW', shared_with_managed=True,
            permissions=['assets.view_asset', 'assets.change_asset', 'assets.delete_asset'],
        )
        ro_role = Role.objects.create(
            tenant=self.provider, name='MO Asset RO', shared_with_managed=True,
            permissions=['assets.view_asset'],
        )
        bulk_tech = User.objects.create_user(username='mo_bulk', password='pw')
        grant(
            bulk_tech, self.provider, rw_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_a],
        )
        grant(
            bulk_tech, self.provider, ro_role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[self.cust_b],
        )

        mfr = Manufacturer.objects.create(name='MO Mfr', slug='mo-mfr')
        atype = AssetType.objects.create(manufacturer=mfr, model='MO Model')
        status = StatusLabel.objects.create(
            name='MO Ready', slug='mo-ready', type=StatusLabel.TYPE_DEPLOYABLE,
        )
        asset_a = Asset.objects.create(
            name='MO Asset A', asset_tag='MO-A-1', asset_type=atype,
            status=status, tenant=self.cust_a,
        )
        asset_b = Asset.objects.create(
            name='MO Asset B', asset_tag='MO-B-1', asset_type=atype,
            status=status, tenant=self.cust_b,
        )

        self._login_group(bulk_tech, self.group)
        response = self.client.post(reverse('assets:asset_bulk_delete'), {
            'pk': [asset_a.pk, asset_b.pk], '_confirm': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(
            Asset._base_manager.get(pk=asset_a.pk).deleted_at,
            "asset in the perm-holding tenant must be deleted",
        )
        self.assertIsNone(
            Asset._base_manager.get(pk=asset_b.pk).deleted_at,
            "asset in the view-only tenant must survive the bulk delete",
        )
