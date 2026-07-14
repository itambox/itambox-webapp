"""Follow-up regressions for canonical grant scopes and permission caching."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant


User = get_user_model()


class CanonicalScopeFollowupTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Followup Provider', slug='followup-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Followup Customer', slug='followup-customer', managed_by=self.provider,
        )
        self.other_customer = Tenant.objects.create(
            name='Followup Other', slug='followup-other', managed_by=self.provider,
        )
        self.user = User.objects.create_user(username='followup-tech')
        self.membership = Membership.objects.create(
            user=self.user, tenant=self.provider,
        )
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Followup reader',
            permissions=['assets.view_asset'],
        )
        self.grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.role,
        )
        self.scope = RoleGrantScope.objects.create(
            role_grant=self.grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )

    def test_prefetch_does_not_change_scope_coverage(self):
        plain = RoleGrant.objects.get(pk=self.grant.pk)
        prefetched = RoleGrant.objects.prefetch_related(
            'scopes', 'scopes__tenant', 'scopes__tenant_group',
        ).get(pk=self.grant.pk)

        self.assertEqual(
            plain.covers_tenant(self.customer),
            prefetched.covers_tenant(self.customer),
        )
        self.assertEqual(
            plain.scoped_tenant_ids(),
            prefetched.scoped_tenant_ids(),
        )

    def test_scope_creation_invalidates_permission_cache(self):
        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.other_customer))

        RoleGrantScope.objects.create(
            role_grant=self.grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.other_customer,
        )

        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.other_customer))

    def test_scope_deletion_invalidates_permission_cache(self):
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.customer))

        self.scope.delete()

        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.customer))

    def test_inactive_membership_disables_every_scope(self):
        self.assertTrue(self.user.has_perm('assets.view_asset', obj=self.customer))

        self.membership.is_active = False
        self.membership.save(update_fields=['is_active'])

        self.assertFalse(self.user.has_perm('assets.view_asset', obj=self.customer))
