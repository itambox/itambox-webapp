"""WS5 regression suite — tenant-group scope uses the canonical accessible set.

The Stage-3 switcher lists reach-derived tenants (no local membership) and offers
their group a "Show All" link. ``filter_by_tenant()`` used to scope a non-superuser
group via direct ``Membership`` rows only, so those managed / UserGroup-derived
tenants vanished after the click. Group scope now intersects
``accessible_tenant_ids(user)`` (direct + UserGroup + managed reach) with the
selected group's subtree, and TenantGroup visibility derives from the same set.
See ``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §5.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import (
    set_current_tenant, set_current_tenant_group, set_current_membership,
)
from core.tests.mixins import grant
from itambox.middleware import _current_user
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()


class GroupScopeAccessibleTenantsTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        #   region ── region_west  (custC lives in region_west)
        self.region = TenantGroup.objects.create(name='Region', slug='ws5-region')
        self.region_west = TenantGroup.objects.create(
            name='Region West', slug='ws5-west', parent=self.region,
        )
        self.provider = Tenant.objects.create(
            name='WS5 Provider', slug='ws5-p', is_provider=True,
        )  # deliberately NOT in the region group
        self.cust_a = Tenant.objects.create(
            name='WS5 A', slug='ws5-a', managed_by=self.provider, group=self.region,
        )
        self.cust_b = Tenant.objects.create(
            name='WS5 B', slug='ws5-b', managed_by=self.provider, group=self.region,
        )
        self.cust_c = Tenant.objects.create(
            name='WS5 C', slug='ws5-c', managed_by=self.provider, group=self.region_west,
        )
        self.tech_role = Role.objects.create(tenant=self.provider, name='Tech', permissions=[])
        self.superuser = User.objects.create_superuser(
            username='ws5_su', email='ws5_su@x.com', password='pw',
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        _current_user.set(None)

    def _visible_tenant_slugs(self, user, group):
        _current_user.set(user)
        set_current_tenant(None)
        set_current_tenant_group(group)
        return set(Tenant.objects.values_list('slug', flat=True))

    def _managed_staff(self, username, assigned):
        user = User.objects.create_user(username=username, password='pw')
        grant(
            user, self.provider, self.tech_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=assigned,
        )
        return user

    def test_managed_reach_tenant_stays_visible_under_its_group(self):
        staff = self._managed_staff('ws5_staff_a', [self.cust_a])
        slugs = self._visible_tenant_slugs(staff, self.region)
        self.assertIn('ws5-a', slugs)       # reachable via managed reach
        self.assertNotIn('ws5-b', slugs)    # in the group but not accessible

    def test_usergroup_derived_tenant_stays_visible_under_its_group(self):
        ug_user = User.objects.create_user(username='ws5_ug', password='pw')
        ug = UserGroup.objects.create(name='WS5 Team', slug='ws5-team', tenant=self.provider)
        membership = Membership.objects.create(user=ug_user, tenant=self.provider)
        GroupMembership.objects.create(user_group=ug, membership=membership)
        group_grant = RoleGrant.objects.create(user_group=ug, role=self.tech_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.cust_a,
        )
        slugs = self._visible_tenant_slugs(ug_user, self.region)
        self.assertIn('ws5-a', slugs)
        self.assertNotIn('ws5-b', slugs)

    def test_mixed_direct_and_managed_access_returns_both(self):
        user = User.objects.create_user(username='ws5_mixed', password='pw')
        role_a = Role.objects.create(tenant=self.cust_a, name='A Direct', permissions=[])
        grant(user, self.cust_a, role_a)  # direct membership in A
        grant(
            user, self.provider, self.tech_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[self.cust_b],
        )  # managed reach to B
        slugs = self._visible_tenant_slugs(user, self.region)
        self.assertEqual(slugs, {'ws5-a', 'ws5-b'})

    def test_descendant_group_tenant_is_included(self):
        staff = self._managed_staff('ws5_staff_c', [self.cust_c])
        # Scope to the parent region; cust_c lives in the descendant region_west.
        slugs = self._visible_tenant_slugs(staff, self.region)
        self.assertIn('ws5-c', slugs)

    def test_soft_deleted_accessible_tenant_is_excluded(self):
        staff = self._managed_staff('ws5_staff_del', [self.cust_a, self.cust_b])
        self.cust_b.deleted_at = __import__('django.utils.timezone', fromlist=['now']).now()
        self.cust_b.save(update_fields=['deleted_at'])
        slugs = self._visible_tenant_slugs(staff, self.region)
        self.assertIn('ws5-a', slugs)
        self.assertNotIn('ws5-b', slugs)

    def test_superuser_group_scope_unchanged(self):
        slugs = self._visible_tenant_slugs(self.superuser, self.region)
        # Every non-deleted tenant in the region subtree, regardless of membership.
        self.assertEqual(slugs, {'ws5-a', 'ws5-b', 'ws5-c'})


class GroupVisibilityAccessibleTests(TestCase):
    """TenantGroup visibility for a member derives from accessible tenants too, so a
    managed/UserGroup-reached tenant's group is navigable."""

    def setUp(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        self.root = TenantGroup.objects.create(name='WS5 Root', slug='ws5g-root')
        self.child = TenantGroup.objects.create(name='WS5 Child', slug='ws5g-child', parent=self.root)
        self.provider = Tenant.objects.create(name='WS5G P', slug='ws5g-p', is_provider=True)
        self.cust = Tenant.objects.create(
            name='WS5G C', slug='ws5g-c', managed_by=self.provider, group=self.child,
        )
        self.tech = Role.objects.create(tenant=self.provider, name='Tech', permissions=[])

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        _current_user.set(None)

    def test_managed_only_member_sees_the_reached_tenants_group_and_ancestors(self):
        staff = User.objects.create_user(username='ws5g_staff', password='pw')
        grant(
            staff, self.provider, self.tech,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[self.cust],
        )
        _current_user.set(staff)
        set_current_tenant(self.provider)  # single-tenant scope
        set_current_tenant_group(None)
        slugs = set(TenantGroup.objects.values_list('slug', flat=True))
        # The managed customer sits in `child`; its group + ancestor `root` are visible.
        self.assertIn('ws5g-child', slugs)
        self.assertIn('ws5g-root', slugs)
