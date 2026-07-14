from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.auth.cache import invalidate_user_authorization_cache
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from users.models import GroupMembership, UserGroup


User = get_user_model()


class AuthorizationCacheInvalidationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Cache tenant', slug='cache-tenant')
        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Cache reader',
            permissions=['assets.view_asset'],
        )
        self.first_user = User.objects.create_user(username='cache-first')
        self.second_user = User.objects.create_user(username='cache-second')
        self.first_membership = Membership.objects.create(
            user=self.first_user,
            tenant=self.tenant,
        )
        self.second_membership = Membership.objects.create(
            user=self.second_user,
            tenant=self.tenant,
        )

    def _group_with_grant(self, name):
        group = UserGroup.objects.create(tenant=self.tenant, name=name)
        grant = RoleGrant.objects.create(user_group=group, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        return group, grant

    def test_invalidation_publishes_again_after_transaction_commit(self):
        with mock.patch('core.auth.cache.cache.set') as cache_set:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                invalidate_user_authorization_cache(self.first_user)

            self.assertEqual(cache_set.call_count, 1)
            self.assertEqual(len(callbacks), 1)
            immediate_version = cache_set.call_args.args[1]
            callbacks[0]()

        self.assertEqual(cache_set.call_count, 2)
        self.assertNotEqual(immediate_version, cache_set.call_args.args[1])

    def test_role_grant_principal_reassignment_invalidates_old_and_new_groups(self):
        first_group, grant = self._group_with_grant('First group')
        second_group = UserGroup.objects.create(tenant=self.tenant, name='Second group')
        GroupMembership.objects.create(
            user_group=first_group,
            membership=self.first_membership,
        )
        GroupMembership.objects.create(
            user_group=second_group,
            membership=self.second_membership,
        )
        self.assertTrue(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertFalse(self.second_user.has_perm('assets.view_asset', obj=self.tenant))

        grant.user_group = second_group
        grant.save(update_fields=['user_group'])

        self.assertFalse(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertTrue(self.second_user.has_perm('assets.view_asset', obj=self.tenant))

    def test_group_membership_reassignment_invalidates_old_and_new_members(self):
        group, _grant = self._group_with_grant('Movable membership group')
        group_membership = GroupMembership.objects.create(
            user_group=group,
            membership=self.first_membership,
        )
        self.assertTrue(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertFalse(self.second_user.has_perm('assets.view_asset', obj=self.tenant))

        group_membership.membership = self.second_membership
        group_membership.save(update_fields=['membership'])

        self.assertFalse(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertTrue(self.second_user.has_perm('assets.view_asset', obj=self.tenant))

    def test_membership_user_reassignment_invalidates_old_and_new_users(self):
        self.second_membership.delete()
        grant = RoleGrant.objects.create(
            membership=self.first_membership,
            role=self.role,
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.assertTrue(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertFalse(self.second_user.has_perm('assets.view_asset', obj=self.tenant))

        self.first_membership.user = self.second_user
        self.first_membership.save(update_fields=['user'])

        self.assertFalse(self.first_user.has_perm('assets.view_asset', obj=self.tenant))
        self.assertTrue(self.second_user.has_perm('assets.view_asset', obj=self.tenant))
