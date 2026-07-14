"""Tests for the custom user model's ``can_login`` flag.

``can_login`` gates *interactive* login (password + SSO) and is a separate axis from
``is_active`` (account status) and from API-token access.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.auth import PasswordLoginOnlyBackend
from itambox.api.authentication import TokenAuthentication
from organization.models import Membership, Tenant
from users.models import Token

User = get_user_model()


class CanLoginModelTests(TestCase):
    def test_default_is_true(self):
        user = User.objects.create_user(username='alice', email='alice@example.com', password='pw-12345!')
        self.assertTrue(user.can_login)


class CanLoginPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bob', email='bob@example.com', password='pw-12345!')
        self.backend = PasswordLoginOnlyBackend()

    def test_password_login_allowed_when_can_login_true(self):
        result = self.backend.authenticate(request=None, username='bob', password='pw-12345!')
        self.assertEqual(result, self.user)

    def test_password_login_blocked_when_can_login_false(self):
        self.user.can_login = False
        self.user.save(update_fields=['can_login'])
        result = self.backend.authenticate(request=None, username='bob', password='pw-12345!')
        self.assertIsNone(result)

    def test_can_login_is_independent_of_is_active(self):
        # is_active stays True (account is not suspended) but interactive login is still barred.
        self.user.can_login = False
        self.user.save(update_fields=['can_login'])
        self.assertTrue(self.user.is_active)
        self.assertIsNone(self.backend.authenticate(request=None, username='bob', password='pw-12345!'))


class CanLoginSSOTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sso', email='sso@example.com')

    def _set_can_login(self, value):
        self.user.can_login = value
        self.user.save(update_fields=['can_login'])

    def test_saml_backend_blocks_no_login_user(self):
        from core.auth.saml import TenantSaml2Backend
        self._set_can_login(False)
        with patch('djangosaml2.backends.Saml2Backend.authenticate', return_value=self.user):
            result = TenantSaml2Backend().authenticate(request=None, session_info=None)
        self.assertIsNone(result)

    def test_saml_backend_allows_login_capable_user(self):
        from core.auth.saml import TenantSaml2Backend
        self._set_can_login(True)
        with patch('djangosaml2.backends.Saml2Backend.authenticate', return_value=self.user):
            result = TenantSaml2Backend().authenticate(request=None, session_info=None)
        self.assertEqual(result, self.user)

    def test_oidc_backend_blocks_no_login_user(self):
        from core.auth.oidc import TenantOIDCBackend
        self._set_can_login(False)
        with patch('mozilla_django_oidc.auth.OIDCAuthenticationBackend.authenticate', return_value=self.user):
            result = TenantOIDCBackend().authenticate(request=None)
        self.assertIsNone(result)


class CanLoginTokenSeparationTests(TestCase):
    def test_api_token_auth_unaffected_by_can_login(self):
        """An API token for a can_login=False user still authenticates — token access is
        governed by is_active, not can_login."""
        user = User.objects.create_user(username='svc', email='svc@example.com')
        user.can_login = False  # may never interactively log in...
        user.save(update_fields=['can_login'])
        tenant = Tenant.objects.create(name='Service tenant', slug='service-tenant')
        Membership.objects.create(user=user, tenant=tenant)
        token = Token.objects.create(user=user, tenant=tenant)
        plaintext = token.key

        request = RequestFactory().get('/', HTTP_AUTHORIZATION=f'Token {plaintext}')
        result = TokenAuthentication().authenticate(request)

        self.assertIsNotNone(result)
        self.assertEqual(result[0], user)  # ...but the token still authenticates
