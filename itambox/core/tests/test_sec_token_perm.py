"""Regression tests for the TokenPermissions test-detection bypass (audit F7).

Historically, TokenPermissions.has_permission contained a sys.argv-based
"is this a test run?" check that, when no tenant resolved, silently set the
current tenant to ``Tenant.objects.first()``. That is test logic living in
production permission code: under the test runner it granted an unscoped,
unauthorized user access to the first tenant's data.

These tests prove the bypass is gone: an authenticated non-superuser with no
Membership and no asset-holder profile is denied (403) on a
TokenPermissions-protected endpoint, while a legitimately scoped member of a
tenant still succeeds (200) on the same endpoint.
"""
from types import SimpleNamespace

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from organization.models import Tenant, Role, Membership
from core.managers import set_current_tenant
from core.tests.mixins import grant
from itambox.api.permissions import TokenPermissions

User = get_user_model()


class TokenPermissionsBypassRemovedTests(TestCase):
    def setUp(self):
        # A real tenant exists in the DB so the old bypass would have had
        # something to hand the unscoped user (proving its removal matters).
        self.tenant = Tenant.objects.create(name='Tenant F7', slug='tenant-f7')

        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Viewer-f7',
            permissions=['assets.view_asset'],
        )

        # Legitimate, scoped member of the tenant.
        self.member = User.objects.create_user(
            username='member-f7', password='password123'
        )
        self.membership = grant(self.member, self.tenant, self.role).membership

        # Unscoped user: authenticated, NOT a superuser, with NO membership
        # and NO asset-holder profile -> has no resolvable tenant.
        self.orphan = User.objects.create_user(
            username='orphan-f7', password='password123'
        )

        # A TokenPermissions-protected list endpoint.
        self.list_url = reverse('api:assets_api:asset-list')

    def test_unscoped_user_denied_without_test_bypass(self):
        """No membership + no profile + not superuser -> 403 (not the first
        tenant's data silently handed over)."""
        # Sanity: the orphan genuinely has no resolvable tenant scope.
        self.assertFalse(
            Membership.objects.filter(user=self.orphan).exists()
        )
        self.assertFalse(self.orphan.asset_holder_profiles.exists())
        self.assertFalse(self.orphan.is_superuser)

        self.client.force_login(self.orphan)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 403)

    def test_scoped_member_still_allowed(self):
        """Positive control: a member with the right role + active tenant
        session still gets 200, proving the bypass removal did not over-block
        legitimate access."""
        self.client.force_login(self.member)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)

    def test_legacy_ignore_attribute_cannot_bypass_model_permissions(self):
        request = RequestFactory().get('/api/')
        request.user = self.orphan
        request.auth = None
        view = SimpleNamespace(
            queryset=Role._base_manager.all(),
            _ignore_model_permissions=True,
        )
        set_current_tenant(self.tenant)
        try:
            self.assertFalse(TokenPermissions().has_permission(request, view))
        finally:
            set_current_tenant(None)

    def test_unbound_permission_check_does_not_choose_first_membership(self):
        request = RequestFactory().get('/api/')
        request.user = self.member
        request.auth = None
        view = SimpleNamespace(queryset=Role._base_manager.all())

        self.assertFalse(TokenPermissions().has_permission(request, view))

    def test_token_permission_context_must_match_token_tenant(self):
        other = Tenant.objects.create(name='Other token tenant', slug='other-token-tenant')
        request = RequestFactory().get('/api/')
        request.user = self.member
        request.auth = SimpleNamespace(tenant_id=other.pk, user_id=self.member.pk)
        view = SimpleNamespace(queryset=Role._base_manager.all())
        set_current_tenant(self.tenant)
        try:
            self.assertFalse(TokenPermissions().has_permission(request, view))
        finally:
            set_current_tenant(None)
