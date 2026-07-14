"""Regression tests for release-blocker findings D2-1 and D2-2 in
``SCIMBearerTokenAuthentication`` (users/api/scim/authentication.py).

D2-1: a bearer token minted under one tenant must not authenticate against a
      *different* tenant's SCIM endpoint, even when its user holds a qualifying
      role in that other tenant too.
D2-2: authorization must flow through the role's actual JSON permissions
      (``organization.change_membership`` via ``has_perm``), never a literal
      ``Role.name`` string match — the same magic-string backdoor pattern
      removed from the (since-deleted) invitation flow.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from organization.models import Tenant, Role
from users.models import Token
from rest_framework import status
from core.tests.mixins import grant

User = get_user_model()


class SCIMTokenTenantScopeTests(TestCase):
    """D2-1: token.tenant must match the URL tenant."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        self.user = User.objects.create_user(username="msp_admin", email="msp@x.com")

        # A real, permission-bearing role (not name-based) in BOTH tenants, so the
        # user genuinely qualifies for SCIM provisioning in either tenant on its own —
        # isolating the test to the token-scope check alone.
        role_a = Role.objects.create(
            tenant=self.tenant_a, name="Provisioner",
            permissions=["organization.change_membership"],
        )
        role_b = Role.objects.create(
            tenant=self.tenant_b, name="Provisioner",
            permissions=["organization.change_membership"],
        )
        grant(self.user, self.tenant_a, role_a)
        grant(self.user, self.tenant_b, role_b)

        # Token explicitly scoped to tenant A only.
        self.token_a = Token.objects.create(
            user=self.user, tenant=self.tenant_a,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.headers_a = {'HTTP_AUTHORIZATION': f'Bearer {self.token_a.key}'}

    def test_token_scoped_to_tenant_a_is_rejected_against_tenant_b(self):
        """Fail-before: without the D2-1 fix this returns 200 because the user's
        membership+role in tenant B independently satisfies the (unscoped) check."""
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant_b.slug})
        response = self.client.get(url, **self.headers_a)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_scoped_to_tenant_a_still_works_against_tenant_a(self):
        """Control: the same token remains valid for its own tenant."""
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant_a.slug})
        response = self.client.get(url, **self.headers_a)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SCIMRolePermissionAuthorizationTests(TestCase):
    """D2-2: authorization must be permission-based, not Role.name-based."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Perm Corp", slug="perm-corp")

    def _token_for(self, user):
        token = Token.objects.create(
            user=user, tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        return {'HTTP_AUTHORIZATION': f'Bearer {token.key}'}

    def test_role_named_admin_without_the_permission_is_rejected(self):
        """Fail-before: today this passes purely because the role is named 'Admin',
        even though it grants no user/membership-management permission at all."""
        user = User.objects.create_user(username="thin_admin", email="thin@x.com")
        role = Role.objects.create(
            tenant=self.tenant, name="Admin",
            permissions=["assets.view_asset"],
        )
        grant(user, self.tenant, role)

        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self._token_for(user))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_role_named_owner_without_the_permission_is_rejected(self):
        user = User.objects.create_user(username="thin_owner", email="thin_owner@x.com")
        role = Role.objects.create(
            tenant=self.tenant, name="Owner",
            permissions=["assets.view_asset"],
        )
        grant(user, self.tenant, role)

        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self._token_for(user))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_role_with_the_permission_but_a_different_name_is_accepted(self):
        """Control: a role that actually grants organization.change_membership
        authorizes SCIM access regardless of its display name (name-agnostic)."""
        user = User.objects.create_user(username="real_provisioner", email="real@x.com")
        role = Role.objects.create(
            tenant=self.tenant, name="HRIS Sync",
            permissions=["organization.change_membership"],
        )
        grant(user, self.tenant, role)

        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self._token_for(user))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_bypasses_the_permission_check(self):
        user = User.objects.create_user(username="root", email="root@x.com", is_superuser=True)
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self._token_for(user))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
