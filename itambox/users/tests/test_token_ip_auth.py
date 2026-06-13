from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from itambox.api.authentication import TokenAuthentication
from users.models import Token

User = get_user_model()


class TokenIPRestrictionAuthTests(TestCase):
    """The TokenAuthentication backend must reject tokens used from a source IP
    outside the token's allowed_ips list (NetBox-style restriction)."""

    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _request(self, token, remote_addr):
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = remote_addr
        return request

    def test_no_restriction_allows_any_ip(self):
        token = Token.objects.create(user=self.user)
        user, authed = self.auth.authenticate(self._request(token, '203.0.113.99'))
        self.assertEqual(user, self.user)
        self.assertEqual(authed, token)

    def test_allowed_ip_passes(self):
        token = Token.objects.create(user=self.user, allowed_ips=['192.168.1.0/24'])
        user, _ = self.auth.authenticate(self._request(token, '192.168.1.42'))
        self.assertEqual(user, self.user)

    def test_disallowed_ip_is_rejected(self):
        token = Token.objects.create(user=self.user, allowed_ips=['192.168.1.0/24'])
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(self._request(token, '10.9.9.9'))

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=True, RATELIMIT_NUM_PROXIES=1)
    def test_uses_forwarded_for_when_configured(self):
        token = Token.objects.create(user=self.user, allowed_ips=['198.51.100.0/24'])
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = '10.0.0.1'  # proxy address, ignored
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.user)

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=False)
    def test_spoofed_forwarded_for_ignored_without_proxy_trust(self):
        # Without proxy trust, a forged X-Forwarded-For must not bypass the restriction.
        token = Token.objects.create(user=self.user, allowed_ips=['198.51.100.0/24'])
        request = self.factory.get('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        request.META['REMOTE_ADDR'] = '10.9.9.9'
        request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)


class TokenWriteEnabledAuthTests(TestCase):
    """C1: a read-only (write_enabled=False) token must be rejected for writes."""

    def setUp(self):
        self.user = User.objects.create_user(username='rouser', password='pass')
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _request(self, method, token):
        factory_method = getattr(self.factory, method.lower())
        request = factory_method('/api/', HTTP_AUTHORIZATION=f'Token {token.key}')
        return request

    def test_read_only_token_allows_get(self):
        token = Token.objects.create(user=self.user, write_enabled=False)
        user, _ = self.auth.authenticate(self._request('GET', token))
        self.assertEqual(user, self.user)

    def test_read_only_token_rejected_for_post(self):
        token = Token.objects.create(user=self.user, write_enabled=False)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.assertRaises(exceptions.AuthenticationFailed):
                self.auth.authenticate(self._request(method, token))

    def test_write_token_allows_post(self):
        token = Token.objects.create(user=self.user, write_enabled=True)
        user, _ = self.auth.authenticate(self._request('POST', token))
        self.assertEqual(user, self.user)
