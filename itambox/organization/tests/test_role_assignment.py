"""RoleGrant lifecycle regressions (the former assignment suite)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import effective_permissions


User = get_user_model()


class RoleGrantLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Grant Tenant', slug='grant-tenant')
        self.other = Tenant.objects.create(name='Grant Other', slug='grant-other')
        self.user = User.objects.create_user(username='grant-member')
        self.membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        self.reader = Role.objects.create(
            tenant=self.tenant,
            name='Grant reader',
            permissions=['assets.view_asset'],
        )
        self.editor = Role.objects.create(
            tenant=self.tenant,
            name='Grant editor',
            permissions=['assets.change_asset'],
        )

    def test_membership_carries_multiple_independent_role_grants(self):
        reader_grant = grant(self.user, self.tenant, self.reader)
        editor_grant = grant(self.user, self.tenant, self.editor)

        self.assertEqual(
            set(self.membership.role_grants.values_list('pk', flat=True)),
            {reader_grant.pk, editor_grant.pk},
        )
        self.assertEqual(
            effective_permissions(self.user, self.tenant),
            frozenset({'assets.view_asset', 'assets.change_asset'}),
        )

    def test_removing_one_grant_preserves_the_other_role(self):
        reader_grant = grant(self.user, self.tenant, self.reader)
        grant(self.user, self.tenant, self.editor)

        reader_grant.delete()

        self.assertEqual(
            effective_permissions(self.user, self.tenant),
            frozenset({'assets.change_asset'}),
        )

    def test_deleting_membership_cascades_grants_and_scopes(self):
        role_grant = grant(self.user, self.tenant, self.reader)
        scope_pk = role_grant.scopes.get().pk
        grant_pk = role_grant.pk

        self.membership.delete()

        self.assertFalse(RoleGrant.objects.filter(pk=grant_pk).exists())
        self.assertFalse(RoleGrantScope.objects.filter(pk=scope_pk).exists())

    def test_soft_deleted_role_leaves_audit_grant_but_revokes_permissions(self):
        role_grant = grant(self.user, self.tenant, self.reader)

        self.reader.delete()

        self.assertTrue(RoleGrant.objects.filter(pk=role_grant.pk).exists())
        self.assertTrue(role_grant.scopes.exists())
        self.assertEqual(effective_permissions(self.user, self.tenant), frozenset())

    def test_inactive_membership_retains_grant_but_revokes_permissions(self):
        role_grant = grant(self.user, self.tenant, self.reader)

        self.membership.is_active = False
        self.membership.save(update_fields=['is_active'])

        self.assertTrue(RoleGrant.objects.filter(pk=role_grant.pk).exists())
        self.assertEqual(effective_permissions(self.user, self.tenant), frozenset())

    def test_expired_grant_retains_scope_but_revokes_permissions(self):
        role_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.reader,
            valid_until=timezone.now() - timedelta(seconds=1),
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        self.assertEqual(effective_permissions(self.user, self.tenant), frozenset())
        self.assertTrue(role_grant.scopes.exists())

    def test_foreign_role_is_rejected_for_direct_own_grant(self):
        foreign_role = Role.objects.create(
            tenant=self.other,
            name='Foreign reader',
            permissions=['assets.view_asset'],
        )

        with self.assertRaises(ValidationError):
            RoleGrant.objects.create(
                membership=self.membership,
                role=foreign_role,
            )

    def test_shared_provider_role_is_valid_on_managed_customer_membership(self):
        provider = Tenant.objects.create(
            name='Grant Provider', slug='grant-provider', is_provider=True,
        )
        customer = Tenant.objects.create(
            name='Grant Customer', slug='grant-customer', managed_by=provider,
        )
        shared = Role.objects.create(
            tenant=provider,
            name='Shared grant reader',
            permissions=['assets.view_asset'],
            shared_with_managed=True,
        )
        customer_user = User.objects.create_user(username='shared-grant-member')

        role_grant = grant(customer_user, customer, shared)

        self.assertTrue(role_grant.scopes.filter(
            scope_type=RoleGrantScope.SCOPE_OWN,
        ).exists())
        self.assertEqual(
            effective_permissions(customer_user, customer),
            frozenset({'assets.view_asset'}),
        )

    def test_elevated_direct_grant_requires_reason_and_expiration(self):
        with self.assertRaises(ValidationError) as context:
            RoleGrant.objects.create(
                membership=self.membership,
                role=self.editor,
            )

        self.assertIn('reason', context.exception.message_dict)
        self.assertIn('valid_until', context.exception.message_dict)
