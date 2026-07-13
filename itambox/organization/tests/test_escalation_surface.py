"""Privilege-escalation guards for canonical grants and group inheritance."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.auth.guards import validate_group_membership_grant, validate_role_grant
from core.tests.mixins import grant
from organization.models import (
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from users.models import UserGroup


User = get_user_model()


class CanonicalEscalationGuardTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Guard Provider', slug='guard-provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Guard A', slug='guard-a', managed_by=self.provider,
        )
        self.customer_b = Tenant.objects.create(
            name='Guard B', slug='guard-b', managed_by=self.provider,
        )
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name='Guard reader',
            permissions=['assets.view_asset'],
        )
        self.broad_role = Role.objects.create(
            tenant=self.provider,
            name='Guard editor',
            permissions=['assets.view_asset', 'assets.change_asset'],
        )
        self.superuser = User.objects.create_superuser(
            username='guard-root', email='guard-root@example.com', password='pw',
        )

    def make_actor(
        self,
        username,
        permissions,
        *,
        coverage=(),
        coverage_permissions=(),
        all_managed=False,
        own_role_name=None,
    ):
        actor = User.objects.create_user(username=username)
        own_role = Role.objects.create(
            tenant=self.provider,
            name=own_role_name or f'{username} own role',
            permissions=list(permissions),
        )
        grant(actor, self.provider, own_role)
        if coverage or all_managed:
            coverage_role = Role.objects.create(
                tenant=self.provider,
                name=f'{username} coverage role',
                permissions=list(coverage_permissions),
            )
            grant(
                actor,
                self.provider,
                coverage_role,
                reach=RoleGrant.REACH_MANAGED,
                managed_scope=(
                    RoleGrantScope.SCOPE_ALL_MANAGED
                    if all_managed else RoleGrantScope.SCOPE_TENANT
                ),
                assigned_tenants=coverage,
            )
        return actor

    def test_actor_cannot_grant_permission_they_do_not_hold(self):
        actor = self.make_actor('guard-reader', ['assets.view_asset'])

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.broad_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_OWN,
            )

        self.assertIn('assets.change_asset', str(context.exception))

    def test_actor_can_grant_permission_subset_they_hold(self):
        actor = self.make_actor(
            'guard-editor',
            ['assets.view_asset', 'assets.change_asset'],
        )

        validate_role_grant(
            actor,
            self.read_role,
            self.provider,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

    def test_managed_grant_requires_rolegrant_administration_permission(self):
        actor = self.make_actor(
            'guard-no-gate',
            ['assets.view_asset'],
            coverage=[self.customer_a],
        )

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.read_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                requested_tenant_ids={self.customer_a.pk},
            )

        self.assertIn('managed tenants', str(context.exception))

    def test_actor_cannot_grant_managed_coverage_broader_than_their_own(self):
        actor = self.make_actor(
            'guard-narrow',
            ['assets.view_asset', 'organization.add_rolegrant'],
            coverage=[self.customer_a],
        )

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.read_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                requested_tenant_ids={self.customer_a.pk, self.customer_b.pk},
            )

        self.assertIn('outside your own reach', str(context.exception))

    def test_actor_can_grant_managed_subset_of_their_coverage(self):
        actor = self.make_actor(
            'guard-covered',
            ['assets.view_asset', 'organization.add_rolegrant'],
            coverage=[self.customer_a, self.customer_b],
            coverage_permissions=['assets.view_asset'],
        )

        validate_role_grant(
            actor,
            self.read_role,
            self.provider,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            requested_tenant_ids={self.customer_a.pk},
        )

    def test_provider_own_admin_cannot_amplify_read_only_customer_coverage(self):
        actor = self.make_actor(
            'guard-split-admin',
            [
                'assets.view_asset',
                'assets.change_asset',
                'organization.add_rolegrant',
            ],
            coverage=[self.customer_b],
            coverage_permissions=['assets.view_asset'],
            own_role_name='Provider Admin',
        )

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.broad_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                requested_tenant_ids={self.customer_b.pk},
            )

        self.assertIn('assets.change_asset', str(context.exception))

    def test_provider_admin_can_delegate_permissions_held_in_customer(self):
        actor = self.make_actor(
            'guard-authorized-admin',
            [
                'assets.view_asset',
                'assets.change_asset',
                'organization.add_rolegrant',
            ],
            coverage=[self.customer_b],
            coverage_permissions=[
                'assets.view_asset',
                'assets.change_asset',
            ],
        )

        validate_role_grant(
            actor,
            self.broad_role,
            self.provider,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            requested_tenant_ids={self.customer_b.pk},
        )

    def test_narrow_actor_cannot_grant_all_managed_scope(self):
        actor = self.make_actor(
            'guard-not-all',
            ['assets.view_asset', 'organization.add_rolegrant'],
            coverage=[self.customer_a],
        )

        with self.assertRaises(ValidationError):
            validate_role_grant(
                actor,
                self.read_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
                requested_tenant_ids=None,
            )

    def test_narrow_actor_cannot_grant_dynamically_expanding_group_scope(self):
        scope_group = TenantGroup.objects.create(
            name='Guard dynamic group',
            slug='guard-dynamic-group',
        )
        self.customer_a.group = scope_group
        self.customer_a.save(update_fields=['group'])
        actor = self.make_actor(
            'guard-not-dynamic',
            ['assets.view_asset', 'organization.add_rolegrant'],
            coverage=[self.customer_a],
        )

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.read_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
                requested_tenant_ids={self.customer_a.pk},
            )

        self.assertIn('dynamic managed-tenant scope', str(context.exception))

    def test_dynamic_scope_requires_matching_all_managed_permissions(self):
        actor = self.make_actor(
            'guard-empty-all',
            ['assets.view_asset', 'organization.add_rolegrant'],
            all_managed=True,
            coverage_permissions=[],
        )

        with self.assertRaises(ValidationError) as context:
            validate_role_grant(
                actor,
                self.read_role,
                self.provider,
                scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
            )

        self.assertIn('assets.view_asset', str(context.exception))

    def test_dynamic_scope_accepts_matching_all_managed_authority(self):
        actor = self.make_actor(
            'guard-reader-all',
            ['assets.view_asset', 'organization.add_rolegrant'],
            all_managed=True,
            coverage_permissions=['assets.view_asset'],
        )

        validate_role_grant(
            actor,
            self.read_role,
            self.provider,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

    def test_group_membership_guard_checks_inherited_managed_coverage(self):
        actor = self.make_actor(
            'guard-group-narrow',
            ['assets.view_asset', 'organization.add_rolegrant'],
            coverage=[self.customer_a],
        )
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Guard broad group',
        )
        group_grant = RoleGrant.objects.create(user_group=group, role=self.read_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        with self.assertRaises(ValidationError):
            validate_group_membership_grant(actor, group)

    def test_superuser_bypasses_permission_and_coverage_guard(self):
        validate_role_grant(
            self.superuser,
            self.broad_role,
            self.provider,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
            requested_tenant_ids=None,
        )
