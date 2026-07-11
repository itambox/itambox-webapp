"""Phase 1 cross-tenant boundary tests for the Users API (F1).

UserViewSet/GroupViewSet expose models with no `tenant` field, so the global
StrictTenantPermission passes through and the unscoped base queryset would leak
every tenant's users (incl. is_staff/email) and groups. These tests assert the
get_queryset() overrides scope list output to the requester's active tenant.

Auth/fixture pattern mirrors core/tests/test_security_boundaries.py: real
Tenant + Role + Membership rows, force_login, and an
`active_tenant_id` session value that TenantMiddleware turns into the active
tenant for the request.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from organization.models import Tenant, Role
from core.tests.mixins import grant

User = get_user_model()


class UsersApiCrossTenantTestCase(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Requesting user is a member of tenant B and may view users/groups.
        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.role_b = Role.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['users.view_user', 'auth.view_group'],
        )
        self.membership_b = grant(self.user_b, self.tenant_b, self.role_b).membership

        # A user that belongs ONLY to tenant A — must be invisible to user_b.
        self.user_a = User.objects.create_user(
            username='user_a', password='password123', email='a@example.com', is_staff=True,
        )
        self.role_a = Role.objects.create(
            tenant=self.tenant_a, name='Admin', permissions=['users.view_user'],
        )
        self.membership_a = grant(self.user_a, self.tenant_a, self.role_a).membership

        # Groups: one whose only member is in tenant A, one with a tenant-B member.
        self.group_a = Group.objects.create(name='Group A only')
        self.group_a.user_set.add(self.user_a)
        self.group_b = Group.objects.create(name='Group B')
        self.group_b.user_set.add(self.user_b)

    def _login_b(self):
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_b.pk
        session.save()

    def test_user_list_excludes_cross_tenant_user(self):
        self._login_b()
        url = reverse('api:users_api:user-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        usernames = {row['username'] for row in response.json()['results']}
        # Own-tenant member is present; the tenant-A-only user is excluded.
        self.assertIn('user_b', usernames)
        self.assertNotIn('user_a', usernames)

    def test_group_list_excludes_cross_tenant_group(self):
        self._login_b()
        url = reverse('api:users_api:group-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        names = {row['name'] for row in response.json()['results']}
        # The group whose only member belongs to tenant A is not visible.
        self.assertIn('Group B', names)
        self.assertNotIn('Group A only', names)
