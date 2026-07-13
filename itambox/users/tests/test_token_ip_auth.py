from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from itambox.api.authentication import TokenAuthentication
from organization.models import Membership, Tenant
from users.models import Token

User = get_user_model()


class TokenIPRestrictionAuthTests(TestCase):
    """The TokenAuthentication backend must reject tokens used from a source IP
    outside the token's allowed_ips list (NetBox-style restriction)."""

    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.tenant = Tenant.objects.create(name='API tenant', slug='api-tenant')
        Membership.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _token(self, **kwargs):
        return Token.objects.create(user=self.user, tenant=self.tenant, **kwargs)

    def _request(self, token, remote_addr):
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = remote_addr
        return request

    def test_no_restriction_allows_any_ip(self):
        token = self._token()
        user, authed = self.auth.authenticate(self._request(token, '203.0.113.99'))
        self.assertEqual(user, self.user)
        self.assertEqual(authed, token)

    def test_allowed_ip_passes(self):
        token = self._token(allowed_ips=['192.168.1.0/24'])
        user, _ = self.auth.authenticate(self._request(token, '192.168.1.42'))
        self.assertEqual(user, self.user)

    def test_disallowed_ip_is_rejected(self):
        token = self._token(allowed_ips=['192.168.1.0/24'])
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(self._request(token, '10.9.9.9'))

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=True, RATELIMIT_NUM_PROXIES=1)
    def test_uses_forwarded_for_when_configured(self):
        token = self._token(allowed_ips=['198.51.100.0/24'])
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = '10.0.0.1'  # proxy address, ignored
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.user)

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=False)
    def test_spoofed_forwarded_for_ignored_without_proxy_trust(self):
        # Without proxy trust, a forged X-Forwarded-For must not bypass the restriction.
        token = self._token(allowed_ips=['198.51.100.0/24'])
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = '10.9.9.9'
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)


class TokenWriteEnabledAuthTests(TestCase):
    """C1: a read-only (write_enabled=False) token must be rejected for writes."""

    def setUp(self):
        self.user = User.objects.create_user(username='rouser', password='pass')
        self.tenant = Tenant.objects.create(name='RO tenant', slug='ro-tenant')
        Membership.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _token(self, **kwargs):
        return Token.objects.create(user=self.user, tenant=self.tenant, **kwargs)

    def _request(self, method, token):
        factory_method = getattr(self.factory, method.lower())
        request = factory_method('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        return request

    def test_read_only_token_allows_get(self):
        token = self._token(write_enabled=False)
        user, _ = self.auth.authenticate(self._request('GET', token))
        self.assertEqual(user, self.user)

    def test_read_only_token_rejected_for_post(self):
        token = self._token(write_enabled=False)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.assertRaises(exceptions.AuthenticationFailed):
                self.auth.authenticate(self._request(method, token))

    def test_write_token_allows_post(self):
        token = self._token(write_enabled=True)
        user, _ = self.auth.authenticate(self._request('POST', token))
        self.assertEqual(user, self.user)


class TokenTenantBindingAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='scoped', password='pass')
        self.tenant = Tenant.objects.create(name='Scoped tenant', slug='scoped-tenant')
        self.membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        self.token = Token.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _request(self):
        return self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_authentication_populates_request_and_active_membership(self):
        request = self._request()
        self.auth.authenticate(request)

        self.assertEqual(request.active_tenant, self.tenant)
        self.assertEqual(request.active_membership, self.membership)
        self.assertIsNone(request.active_tenant_group)

    def test_inactive_membership_revokes_token_authentication(self):
        self.membership.is_active = False
        self.membership.save(update_fields=['is_active'])

        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(self._request())

    def test_deleted_tenant_revokes_token_authentication(self):
        Tenant._base_manager.filter(pk=self.tenant.pk).update(
            deleted_at=timezone.now()
        )

        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(self._request())
