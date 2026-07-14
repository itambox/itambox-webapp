"""End-to-end authorization tests for tenant-owned roles and scoped grants."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

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
from organization.rbac import effective_permissions
from users.models import GroupMembership, UserGroup


User = get_user_model()


class TenantRoleResolutionTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Role Provider', slug='role-provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Role Customer A', slug='role-customer-a', managed_by=self.provider,
        )
        self.customer_z = Tenant.objects.create(
            name='Role Customer Z', slug='role-customer-z', managed_by=self.provider,
        )
        self.outsider = Tenant.objects.create(
            name='Role Outsider', slug='role-outsider',
        )
        self.user = User.objects.create_user(username='tenant-role-tech')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.asset_reader = Role.objects.create(
            tenant=self.provider,
            name='Asset reader',
            permissions=['assets.view_asset'],
        )
        self.tenant_reader = Role.objects.create(
            tenant=self.provider,
            name='Tenant reader',
            permissions=['organization.view_tenant'],
        )

    def scoped_grant(self, role, scope_type, **scope_kwargs):
        role_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=scope_type,
            **scope_kwargs,
        )
        return role_grant

    def fresh_user(self):
        return User.objects.get(pk=self.user.pk)

    def test_own_scope_does_not_project_to_managed_customer(self):
        grant(self.user, self.provider, self.asset_reader)

        self.assertTrue(self.fresh_user().has_perm('assets.view_asset', obj=self.provider))
        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_a))

    def test_specific_scope_projects_full_role_permissions(self):
        self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.assertTrue(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_a))
        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_z))

    def test_different_customers_can_receive_different_roles(self):
        self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.scoped_grant(
            self.tenant_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_z,
        )

        self.assertEqual(
            effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset'}),
        )
        self.assertEqual(
            effective_permissions(self.user, self.customer_z),
            frozenset({'organization.view_tenant'}),
        )

    def test_all_managed_scope_revokes_when_contract_edge_is_removed(self):
        self.scoped_grant(self.asset_reader, RoleGrantScope.SCOPE_ALL_MANAGED)
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.customer_a))

        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])

        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.customer_a))

    def test_tenant_group_scope_walks_descendants(self):
        parent = TenantGroup.objects.create(name='Role parent', slug='role-parent')
        child = TenantGroup.objects.create(
            name='Role child', slug='role-child', parent=parent,
        )
        self.customer_z.group = child
        self.customer_z.save(update_fields=['group'])
        self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT_GROUP,
            tenant_group=parent,
        )

        self.assertTrue(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_z))
        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_a))

    def test_managed_scope_never_reaches_unmanaged_tenant(self):
        self.scoped_grant(self.asset_reader, RoleGrantScope.SCOPE_ALL_MANAGED)

        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.outsider))

    def test_direct_and_group_permissions_are_additive(self):
        self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Tenant role technicians',
        )
        GroupMembership.objects.create(user_group=group, membership=self.membership)
        group_grant = RoleGrant.objects.create(user_group=group, role=self.tenant_reader)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.assertEqual(
            effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset', 'organization.view_tenant'}),
        )

    def test_expired_grant_is_ignored(self):
        role_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.asset_reader,
            valid_until=timezone.now() - timedelta(seconds=1),
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_a))

    def test_cached_grant_expires_during_a_long_lived_user_instance(self):
        now = timezone.now()
        role_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.asset_reader,
            valid_until=now + timedelta(minutes=1),
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        with (
            mock.patch('core.auth.timezone.now', return_value=now),
            mock.patch('organization.rbac.timezone.now', return_value=now),
        ):
            self.assertTrue(
                self.user.has_perm('assets.view_asset', obj=self.customer_a)
            )

        later = now + timedelta(minutes=2)
        with (
            mock.patch('core.auth.timezone.now', return_value=later),
            mock.patch('organization.rbac.timezone.now', return_value=later),
        ):
            self.assertFalse(
                self.user.has_perm('assets.view_asset', obj=self.customer_a)
            )

    def test_role_permission_edit_invalidates_user_cache(self):
        self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.customer_a))

        self.asset_reader.permissions = ['organization.view_tenant']
        _current_user.set(self.user)
        try:
            self.asset_reader.save(update_fields=['permissions'])
        finally:
            _current_user.set(None)

        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.customer_a))
        self.assertTrue(self.user.has_perm('organization.view_tenant', obj=self.customer_a))

    def test_scope_addition_invalidates_user_cache(self):
        role_grant = self.scoped_grant(
            self.asset_reader,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.customer_z))

        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_z,
        )

        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.customer_z))
