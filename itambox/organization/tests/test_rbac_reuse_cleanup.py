"""Shared RBAC presentation helpers consume canonical grants."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.utils import timezone

from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.templatetags.rbac_badges import (
    membership_kind_badge,
    reach_badge,
    shared_role_badge,
)


User = get_user_model()


class RbacBadgeTests(TestCase):
    def test_reach_badge_uses_canonical_reach_vocabulary(self):
        own = str(reach_badge(RoleGrant.REACH_OWN))
        managed = str(reach_badge(RoleGrant.REACH_MANAGED, icon=True))

        self.assertIn('This tenant', own)
        self.assertIn('bg-blue-lt', own)
        self.assertIn('Managed tenants', managed)
        self.assertIn('bg-purple-lt', managed)
        self.assertIn('mdi-domain', managed)

    def test_unknown_reach_fails_closed_to_own_badge(self):
        self.assertEqual(
            str(reach_badge('unknown')),
            str(reach_badge(RoleGrant.REACH_OWN)),
        )

    def test_template_tag_matches_python_helper(self):
        template = Template('{% load rbac_badges %}{% reach_badge reach %}')
        rendered = template.render(Context({'reach': RoleGrant.REACH_MANAGED}))

        self.assertEqual(rendered, str(reach_badge(RoleGrant.REACH_MANAGED)))

    def test_shared_role_badge_is_driven_by_role_flag(self):
        shared = type('RoleLike', (), {'shared_with_managed': True})()
        private = type('RoleLike', (), {'shared_with_managed': False})()

        self.assertIn('Shared', str(shared_role_badge(shared)))
        self.assertEqual(shared_role_badge(private), '')


class MembershipKindBadgeTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Badge Provider', slug='badge-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Badge Customer', slug='badge-customer', managed_by=self.provider,
        )
        self.user = User.objects.create_user(username='badge-tech')
        self.membership = Membership.objects.create(
            user=self.user, tenant=self.provider,
        )
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Badge reader',
            permissions=['assets.view_asset'],
        )

    def make_grant(self, scope_type, *, valid_until=None):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.role,
            valid_until=valid_until,
        )
        kwargs = {'tenant': self.customer} if scope_type == RoleGrantScope.SCOPE_TENANT else {}
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=scope_type,
            **kwargs,
        )
        return grant

    def test_own_scope_is_member(self):
        self.make_grant(RoleGrantScope.SCOPE_OWN)

        self.assertFalse(self.membership.is_staff_membership)
        self.assertIn('Member', str(membership_kind_badge(self.membership)))

    def test_managed_scope_is_staff(self):
        grant = self.make_grant(RoleGrantScope.SCOPE_TENANT)

        self.assertTrue(self.membership.is_staff_membership)
        self.assertIn('Staff', str(membership_kind_badge(self.membership)))
        self.assertEqual(grant.reach, RoleGrant.REACH_MANAGED)

    def test_expired_managed_scope_is_not_staff(self):
        self.make_grant(
            RoleGrantScope.SCOPE_TENANT,
            valid_until=timezone.now() - timedelta(seconds=1),
        )

        self.assertFalse(self.membership.is_staff_membership)
