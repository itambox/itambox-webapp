"""Tests for the cross-tenant UserGroup model and its access model.

Stage-2 RBAC-collapse semantics (RBAC_STAGE2_SPEC.md):
  - UserGroup has an optional owning/SCIM-scope ``tenant`` FK (NULL = global group);
    this does NOT constrain its ``roles`` M2M, which may still span any tenant.
  - A user's effective permissions in a tenant T are the additive union of:
      (a) own-reach RoleAssignments on their ACTIVE Membership in T, and
      (b) the perms of every role whose ``role.tenant == T`` carried by an ACTIVE
          UserGroup the user belongs to — granted INDEPENDENTLY of any membership.
    So being in a group grants access to each of its roles' tenants, with no
    Membership required (the MSP "team" model).
  - Group MANAGEMENT is gated by holding the real ``users.add_usergroup`` /
    ``users.change_usergroup`` permissions in any tenant (``is_global_group_admin``),
    superusers implicitly — the old ``manage_groups`` capability string is gone.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import grant
from organization.access import accessible_tenant_ids
from organization.models import Tenant, Membership, Role
from users.models import UserGroup
from users.views import is_global_group_admin

User = get_user_model()


# --------------------------------------------------------------------------- helpers

def _tenant(name, slug):
    return Tenant.objects.create(name=name, slug=slug)


def _role(tenant, name, perms=None):
    return Role.objects.create(tenant=tenant, name=name, permissions=perms or [])


def _user(username):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="pw")


def _superuser(username):
    return User.objects.create_superuser(username=username, email=f"{username}@example.com", password="pw")


def _membership(user, tenant, roles=None, direct=None, active=True):
    """Build a Membership with own-reach RoleAssignments for ``roles``.

    ``direct`` (a list of permission codenames) is the successor of the deleted
    ``Membership.direct_permissions`` field: it becomes a one-off Role named
    "Direct grants" that is granted alongside the rest.
    """
    m, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
    for role in (roles or []):
        grant(user, tenant, role)
    if direct:
        direct_role = Role.objects.create(
            tenant=tenant, name=f"Direct grants ({user.pk})", permissions=direct,
        )
        grant(user, tenant, direct_role)
    if m.is_active != active:
        m.is_active = active
        m.save()
    return m


def _group(name, roles=None, members=None, active=True):
    g = UserGroup.objects.create(name=name, is_active=active)
    if roles:
        g.roles.set(roles)
    if members:
        g.members.set(members)
    return g


def _grant_group_manager(user):
    """Give ``user`` the standard usergroup-management perms in a fresh tenant — the
    successor of the deleted ``organization.manage_groups`` capability grant (§6:
    ``users.add_usergroup`` / ``users.change_usergroup`` held on any of the user's
    tenants is what ``is_global_group_admin`` now checks)."""
    tenant = Tenant.objects.create(name=f"GroupAdminTenant-{user.pk}", slug=f"group-admin-{user.pk}")
    role = Role.objects.create(
        tenant=tenant, name="Group Admin",
        permissions=["users.add_usergroup", "users.change_usergroup"],
    )
    return grant(user, tenant, role)


class _PermCacheMixin:
    """Clears the per-request permission caches the auth backend stamps on the user."""
    def _flush(self, user):
        for attr in list(user.__dict__):
            if (attr.startswith('_perms_tenant_') or attr.startswith('_tenant_membership_')
                    or attr == '_global_caps_cache'):
                delattr(user, attr)

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)


# --------------------------------------------------------------------------- model

class UserGroupModelTests(_PermCacheMixin, TestCase):
    def test_create_is_global_no_tenant(self):
        g = UserGroup.objects.create(name="Senior Techs", description="MSP L3")
        self.assertEqual(g.name, "Senior Techs")
        self.assertFalse(hasattr(g, 'tenant_id') and g.__dict__.get('tenant_id'))
        self.assertTrue(g.is_active)

    def test_str_is_name(self):
        self.assertEqual(str(UserGroup.objects.create(name="DevOps")), "DevOps")

    def test_name_globally_unique(self):
        UserGroup.objects.create(name="Ops")
        with self.assertRaises(Exception):
            UserGroup.objects.create(name="Ops")

    def test_tenant_scoped_group_name_uniqueness(self):
        # Successor of the deleted Provider model: UserGroup's owning/SCIM-scope FK is
        # now a Tenant (typically an is_provider one for MSP teams).
        tenant_a = Tenant.objects.create(name="MSP A", slug="msp-a", is_provider=True)
        tenant_b = Tenant.objects.create(name="MSP B", slug="msp-b", is_provider=True)

        # 1. Different tenants can use the same group name
        UserGroup.objects.create(name="Ops", tenant=tenant_a)
        UserGroup.objects.create(name="Ops", tenant=tenant_b)

        # 2. Same tenant cannot use the same group name
        with self.assertRaises(Exception):
            UserGroup.objects.create(name="Ops", tenant=tenant_a)

    def test_soft_delete_frees_name(self):
        g = UserGroup.objects.create(name="Temp")
        g.delete()
        self.assertIsNotNone(UserGroup.objects.create(name="Temp").pk)

    def test_roles_may_span_tenants(self):
        ta, tb = _tenant("A", "a"), _tenant("B", "b")
        ra, rb = _role(ta, "RA"), _role(tb, "RB")
        g = _group("Cross", roles=[ra, rb])
        self.assertEqual({r.tenant_id for r in g.roles.all()}, {ta.pk, tb.pk})

    def test_members_m2m(self):
        u = _user("alice")
        self.assertIn(u, _group("Staff", members=[u]).members.all())


# --------------------------------------------------------------------------- cross-tenant access (MSP core)

class CrossTenantAccessTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.ta = _tenant("BoundA", "bound-a")
        self.tb = _tenant("BoundB", "bound-b")
        self.tc = _tenant("BoundC", "bound-c")
        self.user = _user("tech")
        # "Senior Techs" grants admin in A and B (but NOT C). No memberships anywhere.
        self.role_a = _role(self.ta, "A-Admin", ["assets.view_asset", "assets.change_asset"])
        self.role_b = _role(self.tb, "B-Admin", ["assets.view_asset"])
        self.group = _group("Senior Techs", roles=[self.role_a, self.role_b], members=[self.user])

    def test_group_grants_access_without_membership(self):
        self.assertEqual(Membership.objects.filter(user=self.user).count(), 0)
        set_current_tenant(self.ta); self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.change_asset"))   # role_a in A
        set_current_tenant(self.tb); self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.view_asset"))     # role_b in B
        self.assertFalse(self.user.has_perm("assets.change_asset"))  # role_b lacks change

    def test_role_tenant_isolation(self):
        # role_a lives in A; it must not grant anything in B or C.
        set_current_tenant(self.tb); self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.change_asset"))
        set_current_tenant(self.tc); self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.view_asset"))

    def test_accessible_tenants_from_group_only(self):
        self.assertEqual(accessible_tenant_ids(self.user), {self.ta.pk, self.tb.pk})

    def test_obj_path_boundary(self):
        """has_perm(obj=...) grants in the role's tenant, denies in others — no membership."""
        class FakeObj:
            def __init__(self, tenant): self.tenant = tenant
        self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.view_asset", obj=FakeObj(self.ta)))
        self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.view_asset", obj=FakeObj(self.tc)))


# --------------------------------------------------------------------------- union + membership independence

class PermissionUnionTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("U", "u")
        self.user = _user("u1")

    def test_union_of_all_three_sources(self):
        rdirect = _role(self.t, "Direct", ["assets.view_asset"])
        rgroup = _role(self.t, "GroupR", ["inventory.view_accessory"])
        m = _membership(self.user, self.t, roles=[rdirect], direct=["software.view_softwareversion"])
        _group("G", roles=[rgroup], members=[self.user])
        set_current_tenant(self.t); set_current_membership(m); self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.view_asset"))            # direct role
        self.assertTrue(self.user.has_perm("software.view_softwareversion"))  # direct grant
        self.assertTrue(self.user.has_perm("inventory.view_accessory"))    # group role
        self.assertFalse(self.user.has_perm("assets.delete_asset"))

    def test_multi_role_membership(self):
        ra = _role(self.t, "RA", ["assets.view_asset"])
        rb = _role(self.t, "RB", ["assets.add_asset"])
        m = _membership(self.user, self.t, roles=[ra, rb])
        set_current_tenant(self.t); set_current_membership(m); self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.view_asset"))
        self.assertTrue(self.user.has_perm("assets.add_asset"))


class MembershipIndependenceTests(_PermCacheMixin, TestCase):
    """Group grants are independent of Membership presence/active state."""
    def setUp(self):
        super().setUp()
        self.t = _tenant("Ind", "ind")
        self.user = _user("ind_user")
        self.group_role = _role(self.t, "GR", ["assets.view_asset"])
        self.group = _group("Team", roles=[self.group_role], members=[self.user])

    def test_no_membership_still_grants_via_group(self):
        set_current_tenant(self.t); self._flush(self.user)
        self.assertTrue(self.user.has_perm("assets.view_asset"))

    def test_suspended_membership_keeps_group_but_drops_own_roles(self):
        own_role = _role(self.t, "Own", ["assets.add_asset"])
        m = _membership(self.user, self.t, roles=[own_role], direct=["assets.delete_asset"], active=False)
        set_current_tenant(self.t); set_current_membership(m); self._flush(self.user)
        # Suspended membership => its own roles + direct grants do NOT apply...
        self.assertFalse(self.user.has_perm("assets.add_asset"))     # own role
        self.assertFalse(self.user.has_perm("assets.delete_asset"))  # direct grant
        # ...but the group still grants (groups are an independent access path).
        self.assertTrue(self.user.has_perm("assets.view_asset"))

    def test_inactive_group_contributes_nothing(self):
        self.group.is_active = False
        self.group.save()
        set_current_tenant(self.t); self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.view_asset"))


class SoftDeletedRoleTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("SD", "sd")
        self.user = _user("sd_user")

    def test_soft_deleted_role_on_group_grants_nothing(self):
        role = _role(self.t, "Doomed", ["assets.view_asset"])
        _group("G", roles=[role], members=[self.user])
        role.delete()
        set_current_tenant(self.t); self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.view_asset"))

    def test_soft_deleted_role_on_membership_grants_nothing(self):
        role = _role(self.t, "Doomed2", ["assets.view_asset"])
        m = _membership(self.user, self.t, roles=[role])
        role.delete()
        set_current_tenant(self.t); set_current_membership(m); self._flush(self.user)
        self.assertFalse(self.user.has_perm("assets.view_asset"))


# --------------------------------------------------------------------------- global "Group Manager" capability

class GroupManagerCapabilityTests(_PermCacheMixin, TestCase):
    def test_capability_resolution(self):
        # The legacy organization.manage_groups grant is now resolved via real
        # users.add_usergroup / users.change_usergroup permissions held in ANY tenant
        # (is_global_group_admin) — the capability-string vocabulary and
        # GlobalCapabilityBackend are both gone (stage-2 collapse).
        plain = _user("plain")
        self.assertFalse(is_global_group_admin(plain))
        assignment = _grant_group_manager(plain)
        plain = User.objects.get(pk=plain.pk)
        self.assertTrue(is_global_group_admin(plain))
        assignment.delete()
        plain = User.objects.get(pk=plain.pk)
        self.assertFalse(is_global_group_admin(plain))

    def test_superuser_is_group_admin_implicitly(self):
        self.assertTrue(is_global_group_admin(_superuser("su")))

    def test_capability_is_not_a_tenant_role_perm(self):
        """A normal tenant role granting other perms does not confer group management."""
        t = _tenant("Cap", "cap")
        u = _user("cap_user")
        m = _membership(u, t, roles=[_role(t, "R", ["assets.view_asset"])])
        set_current_tenant(t); set_current_membership(m); self._flush(u)
        self.assertFalse(is_global_group_admin(u))


# --------------------------------------------------------------------------- form

class UserGroupFormTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.ta = _tenant("FormA", "form-a")
        self.tb = _tenant("FormB", "form-b")
        self.superuser = _superuser("form_su")
        self.user_a = _user("ua")
        self.user_b = _user("ub")

    def test_tenant_field_is_required_during_phase5(self):
        """UserGroup now carries an explicit owning/SCIM-scope ``tenant`` (successor of
        the deleted ``provider`` FK) — present on the form and optional for superusers
        (blank = global group). It does not narrow ``roles``/``members``: those still
        span every tenant, since group permission grants are driven by ``roles`` alone."""
        from users.forms import UserGroupForm
        _role(self.ta, "RA"); _role(self.tb, "RB")
        form = UserGroupForm(user=self.superuser)
        self.assertIn('tenant', form.fields)
        self.assertTrue(form.fields['tenant'].required)
        # roles span all tenants; members are all users
        self.assertEqual(form.fields['roles'].queryset.count(), Role._base_manager.count())
        self.assertEqual(form.fields['members'].queryset.count(), User.objects.count())

    def test_superuser_cannot_bypass_cross_tenant_group_invariant(self):
        from users.forms import UserGroupForm
        ra, rb = _role(self.ta, "RA", ["assets.view_asset"]), _role(self.tb, "RB", ["assets.add_asset"])
        _membership(self.user_a, self.ta)
        data = {
            'name': 'X', 'roles': [ra.pk, rb.pk], 'members': [self.user_a.pk],
            'tenant': self.ta.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.superuser)
        self.assertFalse(form.is_valid())
        self.assertIn('roles', form.errors)

    def test_escalation_guard_blocks_role_with_unheld_perm(self):
        from users.forms import UserGroupForm
        # limited_user holds only view_asset in tenant A.
        limited = _user("limited")
        view_role = _role(self.ta, "ViewOnly", ["assets.view_asset"])
        m = _membership(limited, self.ta, roles=[view_role])
        delete_role = _role(self.ta, "Deleter", ["assets.delete_asset"])
        set_current_tenant(self.ta); set_current_membership(m); self._flush(limited)
        data = {'name': 'Esc', 'roles': [delete_role.pk], 'members': [], 'is_active': True}
        form = UserGroupForm(data=data, user=limited)
        self.assertFalse(form.is_valid())
        errs = ' '.join(e for errlist in form.errors.values() for e in errlist).lower()
        self.assertIn("escalation", errs)


# --------------------------------------------------------------------------- views (global-admin gated)

class UserGroupViewAccessTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("Views", "views")
        self.superuser = _superuser("vw_su")
        self.manager = _user("vw_mgr")
        _grant_group_manager(self.manager)
        self.plain = _user("vw_plain")
        self.group = _group("VG", roles=[_role(self.t, "VR", ["assets.view_asset"])])

    def _login(self, user):
        self.client.force_login(user)

    def test_superuser_sees_list(self):
        self._login(self.superuser)
        self.assertEqual(self.client.get(reverse('users:usergroup_list')).status_code, 200)

    def test_group_manager_sees_list(self):
        self._login(self.manager)
        self.assertEqual(self.client.get(reverse('users:usergroup_list')).status_code, 200)

    def test_plain_user_denied(self):
        self._login(self.plain)
        resp = self.client.get(reverse('users:usergroup_list'))
        self.assertIn(resp.status_code, (302, 403))

    def test_superuser_detail_edit_render(self):
        self._login(self.superuser)
        self.assertEqual(self.client.get(reverse('users:usergroup_detail', kwargs={'pk': self.group.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse('users:usergroup_update', kwargs={'pk': self.group.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse('users:usergroup_create')).status_code, 200)

    def test_plain_user_denied_detail_and_edit(self):
        self._login(self.plain)
        self.assertIn(self.client.get(reverse('users:usergroup_detail', kwargs={'pk': self.group.pk})).status_code, (302, 403))
        self.assertIn(self.client.get(reverse('users:usergroup_update', kwargs={'pk': self.group.pk})).status_code, (302, 403))


# --------------------------------------------------------------------------- filterset

class UserGroupFilterSetTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("Filt", "filt")
        self.role_a = _role(self.t, "RoleA", ["assets.view_asset"])
        self.role_b = _role(self.t, "RoleB", ["inventory.view_accessory"])
        self.u1, self.u2 = _user("fu1"), _user("fu2")
        self.g1 = _group("Engineering", roles=[self.role_a], members=[self.u1])
        self.g2 = _group("Support", roles=[self.role_b], members=[self.u2], active=False)
        self.g3 = _group("Management", roles=[self.role_a, self.role_b])

    def _fs(self, data):
        from users.filters import UserGroupFilterSet
        return UserGroupFilterSet(data=data, queryset=UserGroup.objects.all())

    def test_filter_by_role(self):
        f = self._fs({'roles': [self.role_b.pk]})
        self.assertIn(self.g2, f.qs); self.assertIn(self.g3, f.qs); self.assertNotIn(self.g1, f.qs)

    def test_filter_by_member(self):
        f = self._fs({'members': self.u1.pk})
        self.assertIn(self.g1, f.qs); self.assertNotIn(self.g2, f.qs)

    def test_filter_by_is_active(self):
        f = self._fs({'is_active': False})
        self.assertIn(self.g2, f.qs); self.assertNotIn(self.g1, f.qs)

    def test_filter_by_grants_tenant(self):
        f = self._fs({'grants_tenant': self.t.pk})
        # all three groups carry a role in self.t
        self.assertEqual(f.qs.distinct().count(), 3)


# --------------------------------------------------------------------------- Membership.direct_permissions form

class MembershipDirectPermissionsTests(_PermCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("DP", "dp")
        self.superuser = _superuser("dp_su")
        self.limited = _user("dp_limited")
        self.view_role = _role(self.t, "DPView", ["assets.view_asset"])
        self.limited_m = _membership(self.limited, self.t, roles=[self.view_role])
        self.target = _user("dp_target")
        self.target_m = _membership(self.target, self.t)

    def test_direct_permissions_resolve(self):
        # Successor of the deleted Membership.direct_permissions field: a one-off Role
        # scoped to just this grant, assigned like any other role via RoleAssignment.
        direct_role = Role.objects.create(
            tenant=self.t, name="Direct grants", permissions=["assets.view_asset"],
        )
        grant(self.target, self.t, direct_role)
        set_current_tenant(self.t); set_current_membership(self.target_m); self._flush(self.target)
        self.assertTrue(self.target.has_perm("assets.view_asset"))

    # NOTE: the form-level ``direct_permissions`` tests were removed with fix #4 (§3-D): the
    # raw-JSON ``direct_permissions`` textarea no longer exists on MembershipForm (the model
    # column and the backend that reads it are retained — see test_direct_permissions_resolve
    # above). Role-based escalation on the form is covered by
    # organization/tests/test_membership_form_no_json.py and test_escalation_surface.py.


# --------------------------------------------------------------------------- per-request cache

class PermCacheTests(_PermCacheMixin, TestCase):
    def test_second_has_perm_uses_cache(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        t = _tenant("Cache", "cache")
        u = _user("cache_u")
        m = _membership(u, t, roles=[_role(t, "CR", ["assets.view_asset"])])
        set_current_tenant(t); set_current_membership(m); self._flush(u)
        u.has_perm("assets.view_asset")
        with CaptureQueriesContext(connection) as ctx:
            self.assertTrue(u.has_perm("assets.view_asset"))
        self.assertEqual(len(ctx.captured_queries), 0)
