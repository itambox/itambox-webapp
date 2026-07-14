"""Workspace navigation follows canonical accessible-tenant resolution."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from organization.access import accessible_tenant_ids, managed_accessible_tenant_ids
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)


User = get_user_model()


class CanonicalNavigationTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Navigation Provider', slug='navigation-provider', is_provider=True,
        )
        self.group = TenantGroup.objects.create(
            name='Navigation group', slug='navigation-group',
        )
        self.child_group = TenantGroup.objects.create(
            name='Navigation child', slug='navigation-child', parent=self.group,
        )
        self.customer_a = Tenant.objects.create(
            name='Navigation A', slug='navigation-a', managed_by=self.provider,
            group=self.group,
        )
        self.customer_b = Tenant.objects.create(
            name='Navigation B', slug='navigation-b', managed_by=self.provider,
            group=self.child_group,
        )
        self.unrelated = Tenant.objects.create(
            name='Navigation unrelated', slug='navigation-unrelated',
        )
        self.user = User.objects.create_user(username='navigation-tech')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Navigation reader',
            permissions=['assets.view_asset'],
        )

    def add_scope(self, scope_type, **kwargs):
        grant = RoleGrant.objects.create(membership=self.membership, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=scope_type,
            **kwargs,
        )
        return grant

    def test_membership_alone_keeps_home_workspace_navigable(self):
        self.assertEqual(accessible_tenant_ids(self.user), {self.provider.pk})
        self.assertEqual(managed_accessible_tenant_ids(self.user), set())

    def test_specific_scope_adds_only_one_customer(self):
        self.add_scope(RoleGrantScope.SCOPE_TENANT, tenant=self.customer_a)

        self.assertEqual(
            accessible_tenant_ids(self.user),
            {self.provider.pk, self.customer_a.pk},
        )
        self.assertEqual(managed_accessible_tenant_ids(self.user), {self.customer_a.pk})

    def test_group_scope_adds_descendant_customers(self):
        self.add_scope(RoleGrantScope.SCOPE_TENANT_GROUP, tenant_group=self.group)

        self.assertEqual(
            accessible_tenant_ids(self.user),
            {self.provider.pk, self.customer_a.pk, self.customer_b.pk},
        )

    def test_all_managed_scope_does_not_include_unrelated_tenant(self):
        self.add_scope(RoleGrantScope.SCOPE_ALL_MANAGED)

        ids = accessible_tenant_ids(self.user)
        self.assertEqual(ids, {self.provider.pk, self.customer_a.pk, self.customer_b.pk})
        self.assertNotIn(self.unrelated.pk, ids)

    def test_deactivated_membership_removes_home_and_projected_workspaces(self):
        self.add_scope(RoleGrantScope.SCOPE_ALL_MANAGED)
        self.membership.is_active = False
        self.membership.save(update_fields=['is_active'])

        self.assertEqual(accessible_tenant_ids(self.user), set())
