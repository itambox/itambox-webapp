from datetime import timedelta
import importlib
from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.mfa import user_requires_mfa
from organization.access import tenant_access_report
from organization.models import (
    Membership,
    Role,
    RoleAssignment,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from organization.rbac import (
    new_accessible_tenant_ids,
    new_effective_permissions,
    resolve_effective_permissions,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()


class Phase5RBACModelTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Provider', slug='provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Customer A', slug='customer-a', managed_by=self.provider,
        )
        self.customer_z = Tenant.objects.create(
            name='Customer Z', slug='customer-z', managed_by=self.provider,
        )
        self.other = Tenant.objects.create(name='Other', slug='other')
        self.user = User.objects.create_user(username='technician')
        self.membership = Membership.objects.create(
            user=self.user, tenant=self.provider,
        )
        self.admin_role = Role.objects.create(
            tenant=self.provider,
            name='Senior admin',
            permissions=['assets.view_asset', 'assets.change_asset'],
        )
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name='Read only',
            permissions=['assets.view_asset'],
        )
        self.group = UserGroup.objects.create(
            tenant=self.provider,
            name='Provider technicians',
        )

    def _group_grant(self, role, scope_type, **scope_kwargs):
        grant = RoleGrant.objects.create(user_group=self.group, role=role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=scope_type,
            **scope_kwargs,
        )
        return grant

    def test_group_membership_requires_same_owning_tenant(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
            source=GroupMembership.SOURCE_LDAP,
            external_id='directory-link-1',
        )
        foreign_membership = Membership.objects.create(
            user=self.user, tenant=self.customer_a,
        )

        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(
                user_group=self.group,
                membership=foreign_membership,
            )

    def test_global_group_cannot_receive_group_membership(self):
        global_group = UserGroup.objects.create(name='Legacy global')
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(
                user_group=global_group,
                membership=self.membership,
            )

    def test_role_grant_requires_exactly_one_principal(self):
        with self.assertRaises(ValidationError):
            RoleGrant.objects.create(role=self.read_role)
        with self.assertRaises(ValidationError):
            RoleGrant.objects.create(
                role=self.read_role,
                membership=self.membership,
                user_group=self.group,
            )

    def test_group_may_only_carry_owner_role(self):
        customer_role = Role.objects.create(
            tenant=self.customer_a,
            name='Customer-owned',
            permissions=['assets.view_asset'],
        )
        with self.assertRaises(ValidationError):
            RoleGrant.objects.create(user_group=self.group, role=customer_role)

    def test_elevated_direct_grant_requires_reason_and_future_expiration(self):
        with self.assertRaises(ValidationError) as context:
            RoleGrant.objects.create(
                membership=self.membership,
                role=self.admin_role,
            )
        self.assertIn('reason', context.exception.message_dict)
        self.assertIn('valid_until', context.exception.message_dict)

        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.admin_role,
            reason='Temporary incident escalation',
            valid_until=timezone.now() + timedelta(hours=2),
        )
        self.assertIsNotNone(grant.pk)

    def test_read_only_direct_grant_may_be_permanent(self):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.read_role,
        )
        self.assertIsNone(grant.valid_until)

    def test_provider_group_can_have_different_roles_per_customer(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.admin_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_z,
        )

        self.assertFalse(
            Membership.objects.filter(
                user=self.user,
                tenant__in=[self.customer_a, self.customer_z],
            ).exists()
        )
        self.assertEqual(
            new_effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset', 'assets.change_asset'}),
        )
        self.assertEqual(
            new_effective_permissions(self.user, self.customer_z),
            frozenset({'assets.view_asset'}),
        )

    def test_permissions_are_additive_across_group_and_direct_grants(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        direct_role = Role.objects.create(
            tenant=self.provider,
            name='Temporary asset editor',
            permissions=['assets.change_asset'],
        )
        direct = RoleGrant.objects.create(
            membership=self.membership,
            role=direct_role,
            reason='Approved exception',
            valid_until=timezone.now() + timedelta(hours=1),
        )
        RoleGrantScope.objects.create(
            role_grant=direct,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.assertEqual(
            new_effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset', 'assets.change_asset'}),
        )

    def test_clearing_management_edge_immediately_revokes_projection(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        self.assertEqual(
            new_effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset'}),
        )

        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])
        self.assertEqual(
            new_effective_permissions(self.user, self.customer_a),
            frozenset(),
        )

    def test_unrelated_group_edit_preserves_managed_grant_scopes(self):
        grant = self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.group.name = 'Renamed provider technicians'
        self.group.save(update_fields=['name'])

        grant.refresh_from_db()
        self.assertEqual(grant.scopes.get().tenant_id, self.customer_a.pk)

    def test_shared_provider_role_on_customer_membership_keeps_own_scope_semantics(self):
        customer_user = User.objects.create_user(username='customer-member')
        customer_membership = Membership.objects.create(
            user=customer_user,
            tenant=self.customer_a,
        )
        shared_role = Role.objects.create(
            tenant=self.provider,
            name='Shared provider reader',
            permissions=['assets.view_asset'],
            shared_with_managed=True,
        )
        grant = RoleGrant.objects.create(
            membership=customer_membership,
            role=shared_role,
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        self.assertEqual(
            new_effective_permissions(customer_user, self.customer_a),
            frozenset({'assets.view_asset'}),
        )
        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])
        self.assertEqual(
            new_effective_permissions(customer_user, self.customer_a),
            frozenset(),
        )

    def test_expired_grant_is_inert(self):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.read_role,
            valid_until=timezone.now() - timedelta(seconds=1),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.assertEqual(new_effective_permissions(self.user, self.customer_a), frozenset())

    def test_tenant_group_scope_includes_descendants_only(self):
        parent = TenantGroup.objects.create(name='Parent', slug='parent')
        child = TenantGroup.objects.create(name='Child', slug='child', parent=parent)
        self.customer_z.group = child
        self.customer_z.save(update_fields=['group'])
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT_GROUP,
            tenant_group=parent,
        )

        self.assertEqual(
            new_effective_permissions(self.user, self.customer_z),
            frozenset({'assets.view_asset'}),
        )
        self.assertEqual(new_effective_permissions(self.user, self.customer_a), frozenset())

    def test_accessible_tenants_include_scoped_managed_targets(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_z,
        )
        self.assertEqual(
            new_accessible_tenant_ids(self.user),
            {self.provider.pk, self.customer_z.pk},
        )

    @override_settings(RBAC_RESOLVER_MODE='new')
    def test_auth_backend_uses_role_grants_after_cutover(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.assertTrue(
            self.user.has_perm('assets.view_asset', obj=self.customer_a)
        )
        self.assertFalse(
            self.user.has_perm('assets.change_asset', obj=self.customer_a)
        )

    @override_settings(RBAC_RESOLVER_MODE='new')
    def test_access_report_uses_group_memberships_after_cutover(self):
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        self._group_grant(
            self.read_role,
            RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        report = tenant_access_report(self.customer_a, external_only=True)

        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]['user'], self.user)
        self.assertEqual(report[0]['groups'], [self.group.name])
        self.assertEqual(report[0]['sources'], ['group', 'managed'])
        self.assertEqual(report[0]['permissions'], ['assets.view_asset'])

    def test_role_grant_only_privilege_is_mfa_protected(self):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.admin_role,
            reason='Temporary privileged work',
            valid_until=timezone.now() + timedelta(hours=1),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        self.assertTrue(user_requires_mfa(self.user))


class Phase5LegacyShadowAndComparisonTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Shadow Provider', slug='shadow-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Shadow Customer',
            slug='shadow-customer',
            managed_by=self.provider,
        )
        self.user = User.objects.create_user(username='shadow-user')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Shadow reader',
            permissions=['assets.view_asset'],
        )

    def test_role_assignment_and_scope_are_shadowed_and_deleted(self):
        assignment = RoleAssignment.objects.create(
            membership=self.membership,
            role=self.role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
        )
        grant = RoleGrant.objects.get(legacy_assignment=assignment)
        self.assertFalse(grant.scopes.exists())

        assignment.assigned_tenants.add(self.customer)
        scope = grant.scopes.get()
        self.assertEqual(scope.scope_type, RoleGrantScope.SCOPE_TENANT)
        self.assertEqual(scope.tenant, self.customer)

        assignment.delete()
        self.assertFalse(RoleGrant.objects.filter(pk=grant.pk).exists())

    def test_valid_legacy_group_links_are_shadowed(self):
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Shadow group',
        )
        group.roles.add(self.role)
        group.members.add(self.user)

        grant = RoleGrant.objects.get(user_group=group, role=self.role)
        self.assertEqual(grant.scopes.get().scope_type, RoleGrantScope.SCOPE_OWN)
        self.assertTrue(
            GroupMembership.objects.filter(
                user_group=group,
                membership=self.membership,
            ).exists()
        )

    def test_data_migration_rebuilds_derivable_shadow_rows(self):
        assignment = RoleAssignment.objects.create(
            membership=self.membership,
            role=self.role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
        )
        assignment.assigned_tenants.add(self.customer)
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Backfill group',
        )
        group.roles.add(self.role)
        group.members.add(self.user)
        RoleGrantScope.objects.all().delete()
        RoleGrant.objects.all().delete()
        GroupMembership.objects.all().delete()

        migration = importlib.import_module(
            'organization.migrations.0039_backfill_phase5_rbac'
        )
        migration.backfill_phase5_rbac(apps, None)

        assignment_grant = RoleGrant.objects.get(legacy_assignment=assignment)
        self.assertEqual(
            assignment_grant.scopes.get().tenant_id,
            self.customer.pk,
        )
        self.assertTrue(RoleGrant.objects.filter(user_group=group, role=self.role).exists())
        self.assertTrue(
            GroupMembership.objects.filter(
                user_group=group,
                membership=self.membership,
            ).exists()
        )

    def test_comparison_command_is_clean_for_derivable_legacy_data(self):
        assignment = RoleAssignment.objects.create(
            membership=self.membership,
            role=self.role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
        )
        assignment.assigned_tenants.add(self.customer)
        output = StringIO()

        call_command(
            'compare_rbac_resolvers',
            user_id=self.user.pk,
            stdout=output,
        )

        self.assertIn('0 disagreement(s)', output.getvalue())

    def test_comparison_command_fails_on_non_derivable_cross_owner_group(self):
        customer_role = Role.objects.create(
            tenant=self.customer,
            name='Legacy customer role',
            permissions=['assets.change_asset'],
        )
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Legacy cross-owner group',
        )
        group.members.add(self.user)
        group.roles.add(customer_role)

        with self.assertRaises(CommandError):
            call_command(
                'compare_rbac_resolvers',
                user_id=self.user.pk,
                tenant_id=self.customer.pk,
                stdout=StringIO(),
                stderr=StringIO(),
            )

    @override_settings(RBAC_RESOLVER_MODE='compare')
    def test_compare_mode_returns_legacy_decision(self):
        assignment = RoleAssignment.objects.create(
            membership=self.membership,
            role=self.role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
        )
        assignment.assigned_tenants.add(self.customer)

        self.assertEqual(
            resolve_effective_permissions(self.user, self.customer),
            frozenset({'assets.view_asset'}),
        )
