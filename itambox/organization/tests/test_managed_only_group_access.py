"""A provider member can work solely through managed RoleGrantScope rows."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import (
    get_current_membership,
    get_current_tenant,
    get_current_tenant_group,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)


User = get_user_model()


class ManagedOnlyGroupAccessTests(TestCase):
    def setUp(self):
        self.group = TenantGroup.objects.create(name='Managed region', slug='managed-region')
        self.other_group = TenantGroup.objects.create(
            name='Managed other', slug='managed-other',
        )
        self.provider = Tenant.objects.create(
            name='Managed Provider', slug='managed-provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Managed A', slug='managed-a', managed_by=self.provider,
            group=self.group,
        )
        self.customer_b = Tenant.objects.create(
            name='Managed B', slug='managed-b', managed_by=self.provider,
            group=self.group,
        )
        self.customer_c = Tenant.objects.create(
            name='Managed C', slug='managed-c', managed_by=self.provider,
            group=self.other_group,
        )
        self.user = User.objects.create_user(username='managed-only-tech')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Managed-only reader',
            permissions=['organization.view_membership', 'assets.view_asset'],
        )
        self.grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.role,
        )
        for tenant in (self.customer_a, self.customer_b):
            RoleGrantScope.objects.create(
                role_grant=self.grant,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=tenant,
            )
        self.clear_context()

    def tearDown(self):
        self.clear_context()

    @staticmethod
    def clear_context():
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)

    def fresh_user(self):
        return User.objects.get(pk=self.user.pk)

    def test_managed_permissions_do_not_require_customer_memberships(self):
        self.assertFalse(
            Membership.objects.filter(
                user=self.user,
                tenant__in=[self.customer_a, self.customer_b],
            ).exists()
        )
        self.assertTrue(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_a))
        self.assertTrue(self.fresh_user().has_perm('assets.view_asset', obj=self.customer_b))

    def test_provider_has_no_permissions_without_own_scope(self):
        self.assertFalse(self.fresh_user().has_perm('assets.view_asset', obj=self.provider))

    def test_group_ambient_gate_uses_reachable_customers(self):
        set_current_tenant_group(self.group)

        user = self.fresh_user()
        self.assertTrue(user.has_perm('organization.view_membership'))
        self.assertIsNone(get_current_tenant())
        self.assertIsNone(get_current_membership())
        self.assertEqual(get_current_tenant_group(), self.group)

    def test_group_ambient_gate_fails_closed_outside_coverage(self):
        set_current_tenant_group(self.other_group)

        self.assertFalse(self.fresh_user().has_perm('organization.view_membership'))

    def test_single_tenant_ambient_context_stays_single_tenant(self):
        set_current_tenant(self.customer_a)

        self.assertTrue(self.fresh_user().has_perm('assets.view_asset'))
        self.assertEqual(get_current_tenant(), self.customer_a)
        self.assertIsNone(get_current_tenant_group())

    def test_removing_one_scope_revokes_only_that_customer(self):
        self.grant.scopes.get(tenant=self.customer_a).delete()

        user = self.fresh_user()
        self.assertFalse(user.has_perm('assets.view_asset', obj=self.customer_a))
        self.assertTrue(user.has_perm('assets.view_asset', obj=self.customer_b))
