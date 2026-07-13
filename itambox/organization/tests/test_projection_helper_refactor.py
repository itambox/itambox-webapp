"""Canonical projection helpers are defined by RoleGrantScope rows."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)


User = get_user_model()


class RoleGrantProjectionTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Projection Provider', slug='projection-provider', is_provider=True,
        )
        self.parent_group = TenantGroup.objects.create(
            name='Projection parent', slug='projection-parent',
        )
        self.child_group = TenantGroup.objects.create(
            name='Projection child', slug='projection-child', parent=self.parent_group,
        )
        self.customer_a = Tenant.objects.create(
            name='Projection A', slug='projection-a', managed_by=self.provider,
            group=self.parent_group,
        )
        self.customer_b = Tenant.objects.create(
            name='Projection B', slug='projection-b', managed_by=self.provider,
            group=self.child_group,
        )
        self.unmanaged = Tenant.objects.create(
            name='Projection outsider', slug='projection-outsider',
            group=self.child_group,
        )
        self.user = User.objects.create_user(username='projection-tech')
        self.membership = Membership.objects.create(
            user=self.user, tenant=self.provider,
        )
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Projection reader',
            permissions=['assets.view_asset'],
        )

    def make_grant(self, *scopes):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.role,
        )
        for scope_type, target in scopes:
            kwargs = {}
            if scope_type == RoleGrantScope.SCOPE_TENANT:
                kwargs['tenant'] = target
            elif scope_type == RoleGrantScope.SCOPE_TENANT_GROUP:
                kwargs['tenant_group'] = target
            RoleGrantScope.objects.create(
                role_grant=grant,
                scope_type=scope_type,
                **kwargs,
            )
        return grant

    def test_own_scope_covers_only_the_principal_tenant(self):
        grant = self.make_grant((RoleGrantScope.SCOPE_OWN, None))

        self.assertTrue(grant.covers_tenant(self.provider))
        self.assertFalse(grant.covers_tenant(self.customer_a))
        self.assertEqual(grant.scoped_tenant_ids(), set())

    def test_specific_tenant_scope_covers_only_that_managed_tenant(self):
        grant = self.make_grant((RoleGrantScope.SCOPE_TENANT, self.customer_a))

        self.assertTrue(grant.covers_tenant(self.customer_a))
        self.assertFalse(grant.covers_tenant(self.customer_b))
        self.assertEqual(grant.scoped_tenant_ids(), {self.customer_a.pk})

    def test_specific_tenant_scope_ends_with_the_management_edge(self):
        grant = self.make_grant((RoleGrantScope.SCOPE_TENANT, self.customer_a))

        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])

        self.assertFalse(grant.covers_tenant(self.customer_a))
        self.assertEqual(grant.scoped_tenant_ids(), set())

    def test_all_managed_scope_tracks_current_management_edges(self):
        grant = self.make_grant((RoleGrantScope.SCOPE_ALL_MANAGED, None))

        self.assertEqual(
            grant.scoped_tenant_ids(),
            {self.customer_a.pk, self.customer_b.pk},
        )
        self.assertFalse(grant.covers_tenant(self.unmanaged))

        self.customer_b.managed_by = None
        self.customer_b.save(update_fields=['managed_by'])
        self.assertEqual(grant.scoped_tenant_ids(), {self.customer_a.pk})
        self.assertFalse(grant.covers_tenant(self.customer_b))

    def test_tenant_group_scope_includes_descendants(self):
        grant = self.make_grant((
            RoleGrantScope.SCOPE_TENANT_GROUP,
            self.parent_group,
        ))

        self.assertEqual(
            grant.scoped_tenant_ids(),
            {self.customer_a.pk, self.customer_b.pk},
        )
        self.assertFalse(grant.covers_tenant(self.unmanaged))

    def test_soft_deleted_tenant_group_scope_is_inert(self):
        grant = self.make_grant((
            RoleGrantScope.SCOPE_TENANT_GROUP,
            self.parent_group,
        ))

        self.parent_group.delete()

        self.assertFalse(grant.covers_tenant(self.customer_a))
        self.assertFalse(grant.covers_tenant(self.customer_b))
        self.assertEqual(grant.scoped_tenant_ids(), set())
        self.assertTrue(grant.scopes.exists())

    def test_child_group_scope_does_not_include_parent_group_tenant(self):
        grant = self.make_grant((
            RoleGrantScope.SCOPE_TENANT_GROUP,
            self.child_group,
        ))

        self.assertEqual(grant.scoped_tenant_ids(), {self.customer_b.pk})
        self.assertFalse(grant.covers_tenant(self.customer_a))

    def test_multiple_scopes_are_additive(self):
        grant = self.make_grant(
            (RoleGrantScope.SCOPE_OWN, None),
            (RoleGrantScope.SCOPE_TENANT, self.customer_a),
        )

        self.assertTrue(grant.covers_tenant(self.provider))
        self.assertTrue(grant.covers_tenant(self.customer_a))
        self.assertFalse(grant.covers_tenant(self.customer_b))
        self.assertEqual(grant.reach, RoleGrant.REACH_MANAGED)
