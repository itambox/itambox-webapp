"""Phase 3 correction for reopened issue #56.

``build_accessible_tenant_permissions_map`` is a fast-path precompute of
``effective_permissions_with_expiry``: it must agree with the canonical
``RoleGrant.covers_tenant()`` walk on every tenant it maps, never grant more.
An ``ALL_MANAGED``/``TENANT_GROUP``-only grant (no ``SCOPE_OWN``) does not
cover the grant's own principal tenant — ``covers_tenant()`` already encodes
that — so the map must not add the principal tenant just because it is the
grant's owner.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from organization.models import (
    Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup,
)
from organization.rbac import (
    build_accessible_tenant_permissions_map,
    effective_permissions_with_expiry,
)

User = get_user_model()


class TenantPermissionsMapOwnScopeTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Phase3 Provider', slug='phase3-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Phase3 Customer', slug='phase3-customer', managed_by=self.provider,
        )
        self.group = TenantGroup.objects.create(name='Phase3 Group', slug='phase3-group')
        self.grouped_customer = Tenant.objects.create(
            name='Phase3 Grouped Customer', slug='phase3-grouped-customer',
            managed_by=self.provider, group=self.group,
        )
        self.user = User.objects.create_user(username='phase3-tech')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Phase3 reader',
            permissions=['assets.view_asset'],
        )

    def _grant(self, scope_type, tenant_group=None):
        role_grant = RoleGrant.objects.create(membership=self.membership, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=role_grant, scope_type=scope_type, tenant_group=tenant_group,
        )
        return role_grant

    def test_all_managed_only_grant_does_not_leak_into_principal_tenant(self):
        self._grant(RoleGrantScope.SCOPE_ALL_MANAGED)

        perm_map = build_accessible_tenant_permissions_map(self.user)

        self.assertNotIn(self.provider.pk, perm_map)
        self.assertIn(self.customer.pk, perm_map)
        self.assertIn('assets.view_asset', perm_map[self.customer.pk][0])

        # The fast-path map must agree with the canonical covers_tenant() walk.
        self.assertEqual(
            effective_permissions_with_expiry(self.user, self.provider)[0],
            frozenset(),
        )

    def test_tenant_group_only_grant_does_not_leak_into_principal_tenant(self):
        self._grant(RoleGrantScope.SCOPE_TENANT_GROUP, tenant_group=self.group)

        perm_map = build_accessible_tenant_permissions_map(self.user)

        self.assertNotIn(self.provider.pk, perm_map)
        self.assertIn(self.grouped_customer.pk, perm_map)
        self.assertIn('assets.view_asset', perm_map[self.grouped_customer.pk][0])

    def test_warm_map_agrees_with_canonical_lookup_for_principal_tenant(self):
        self._grant(RoleGrantScope.SCOPE_ALL_MANAGED)
        build_accessible_tenant_permissions_map(self.user)

        # effective_permissions_with_expiry short-circuits to the primed map
        # when present; it must not grant the provider its own permissions
        # from an ALL_MANAGED-only grant just because the map is warm.
        permissions, valid_until = effective_permissions_with_expiry(self.user, self.provider)
        self.assertEqual(permissions, frozenset())
        self.assertIsNone(valid_until)

    def test_map_preserves_earliest_valid_until_per_tenant(self):
        near = timezone.now() + timedelta(days=1)
        far = timezone.now() + timedelta(days=30)

        first = self._grant(RoleGrantScope.SCOPE_ALL_MANAGED)
        first.valid_until = far
        first.save(update_fields=['valid_until'])

        second_role = Role.objects.create(
            tenant=self.provider, name='Phase3 reader 2', permissions=['assets.view_asset'],
        )
        second = RoleGrant.objects.create(
            membership=self.membership, role=second_role, valid_until=near,
        )
        RoleGrantScope.objects.create(
            role_grant=second, scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        perm_map = build_accessible_tenant_permissions_map(self.user)

        self.assertEqual(perm_map[self.customer.pk][1], near)
