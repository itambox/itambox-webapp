from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.mfa import user_requires_mfa
from organization.access import tenant_access_report
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from organization.rbac import (
    accessible_tenant_ids,
    effective_permissions,
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

    def test_user_group_requires_owner(self):
        with self.assertRaises((ValidationError, IntegrityError)):
            UserGroup.objects.create(name='Ownerless group')

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

    def test_custom_action_permission_is_elevated_and_mfa_protected(self):
        approver = Role.objects.create(
            tenant=self.provider,
            name='Purchase approver',
            permissions=['procurement.approve_purchaseorder'],
        )
        with self.assertRaises(ValidationError) as context:
            RoleGrant.objects.create(
                membership=self.membership,
                role=approver,
            )
        self.assertIn('reason', context.exception.message_dict)
        self.assertIn('valid_until', context.exception.message_dict)

        role_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=approver,
            reason='Temporary purchase approval duty',
            valid_until=timezone.now() + timedelta(hours=2),
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.assertTrue(user_requires_mfa(self.user))

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
            effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset', 'assets.change_asset'}),
        )
        self.assertEqual(
            effective_permissions(self.user, self.customer_z),
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
            effective_permissions(self.user, self.customer_a),
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
            effective_permissions(self.user, self.customer_a),
            frozenset({'assets.view_asset'}),
        )

        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])
        self.assertEqual(
            effective_permissions(self.user, self.customer_a),
            frozenset(),
        )

    def test_provider_cannot_be_disabled_while_it_has_live_customers(self):
        self.provider.is_provider = False

        with self.assertRaises(ValidationError) as context:
            self.provider.save(update_fields=['is_provider'])

        self.assertIn('is_provider', context.exception.message_dict)

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
            effective_permissions(customer_user, self.customer_a),
            frozenset({'assets.view_asset'}),
        )
        self.customer_a.managed_by = None
        self.customer_a.save(update_fields=['managed_by'])
        self.assertEqual(
            effective_permissions(customer_user, self.customer_a),
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
        self.assertEqual(effective_permissions(self.user, self.customer_a), frozenset())

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
            effective_permissions(self.user, self.customer_z),
            frozenset({'assets.view_asset'}),
        )
        self.assertEqual(effective_permissions(self.user, self.customer_a), frozenset())

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
            accessible_tenant_ids(self.user),
            {self.provider.pk, self.customer_z.pk},
        )

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
