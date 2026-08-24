from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from core.managers import (
    get_current_all_accessible,
    get_current_membership,
    get_current_tenant,
    set_current_all_accessible,
)
from organization.models import Membership, Tenant
from users.api.authentication import TokenAuthentication
from users.models import Token

User = get_user_model()


def assert_authentication_failure(test_case, authenticator, request, message):
    with test_case.assertRaises(exceptions.AuthenticationFailed) as raised:
        authenticator.authenticate(request)
    test_case.assertEqual(str(raised.exception.detail), message)


class TokenIPRestrictionAuthTests(TestCase):
    """The TokenAuthentication backend must reject tokens used from a source IP
    outside the token's allowed_ips list (NetBox-style restriction)."""

    def setUp(self):
        self.user = User.objects.create_user(username="apiuser", password="pass")
        self.tenant = Tenant.objects.create(name="API tenant", slug="api-tenant")
        Membership.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _token(self, **kwargs):
        return Token.objects.create(user=self.user, tenant=self.tenant, **kwargs)

    def _request(self, token, remote_addr):
        request = self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}")
        request.META["REMOTE_ADDR"] = remote_addr
        return request

    def test_no_restriction_allows_any_ip(self):
        token = self._token()
        user, authed = self.auth.authenticate(self._request(token, "203.0.113.99"))
        self.assertEqual(user, self.user)
        self.assertEqual(authed, token)

    def test_allowed_ip_passes(self):
        token = self._token(allowed_ips=["192.168.1.0/24"])
        user, _ = self.auth.authenticate(self._request(token, "192.168.1.42"))
        self.assertEqual(user, self.user)

    def test_disallowed_ip_is_rejected(self):
        token = self._token(allowed_ips=["192.168.1.0/24"])
        assert_authentication_failure(
            self,
            self.auth,
            self._request(token, "10.9.9.9"),
            "Source IP address is not permitted to use this token.",
        )

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=True, RATELIMIT_NUM_PROXIES=1)
    def test_uses_forwarded_for_when_configured(self):
        token = self._token(allowed_ips=["198.51.100.0/24"])
        request = self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}")
        request.META["REMOTE_ADDR"] = "10.0.0.1"  # proxy address, ignored
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.7"
        user, _ = self.auth.authenticate(request)
        self.assertEqual(user, self.user)

    @override_settings(RATELIMIT_USE_X_FORWARDED_FOR=False)
    def test_spoofed_forwarded_for_ignored_without_proxy_trust(self):
        # Without proxy trust, a forged X-Forwarded-For must not bypass the restriction.
        token = self._token(allowed_ips=["198.51.100.0/24"])
        request = self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}")
        request.META["REMOTE_ADDR"] = "10.9.9.9"
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.7"
        assert_authentication_failure(
            self,
            self.auth,
            request,
            "Source IP address is not permitted to use this token.",
        )

    def test_token_header_failures_have_exact_messages(self):
        for header, message in (
            (
                "Token",
                "Invalid token header. No credentials provided.",
            ),
            (
                "Token key contains spaces",
                "Invalid token header. Token string should not contain spaces.",
            ),
        ):
            with self.subTest(header=header):
                request = self.factory.get("/api/", HTTP_AUTHORIZATION=header)
                assert_authentication_failure(self, self.auth, request, message)

        request = self.factory.get("/api/")
        request.META["HTTP_AUTHORIZATION"] = b"Token \xff"
        assert_authentication_failure(
            self,
            self.auth,
            request,
            "Invalid token header. Token string should not contain invalid characters.",
        )

    def test_expiry_check_precedes_ip_check(self):
        token = self._token(
            allowed_ips=["192.168.1.0/24"],
            expires=timezone.now() - timezone.timedelta(seconds=1),
        )
        assert_authentication_failure(
            self,
            self.auth,
            self._request(token, "10.9.9.9"),
            "Token expired.",
        )


class TokenWriteEnabledAuthTests(TestCase):
    """C1: a read-only (write_enabled=False) token must be rejected for writes."""

    def setUp(self):
        self.user = User.objects.create_user(username="rouser", password="pass")
        self.tenant = Tenant.objects.create(name="RO tenant", slug="ro-tenant")
        Membership.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _token(self, **kwargs):
        return Token.objects.create(user=self.user, tenant=self.tenant, **kwargs)

    def _request(self, method, token):
        factory_method = getattr(self.factory, method.lower())
        request = factory_method("/api/", HTTP_AUTHORIZATION=f"Token {token.key}")
        return request

    def test_read_only_token_allows_get(self):
        token = self._token(write_enabled=False)
        user, _ = self.auth.authenticate(self._request("GET", token))
        self.assertEqual(user, self.user)

    def test_read_only_token_rejected_for_post(self):
        token = self._token(write_enabled=False)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                assert_authentication_failure(
                    self,
                    self.auth,
                    self._request(method, token),
                    "This token is read-only and cannot be used for write operations.",
                )

    def test_write_check_precedes_inactive_user_check(self):
        token = self._token(write_enabled=False)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        assert_authentication_failure(
            self,
            self.auth,
            self._request("POST", token),
            "This token is read-only and cannot be used for write operations.",
        )

    def test_write_token_allows_post(self):
        token = self._token(write_enabled=True)
        user, _ = self.auth.authenticate(self._request("POST", token))
        self.assertEqual(user, self.user)


class TokenTenantBindingAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="scoped", password="pass")
        self.tenant = Tenant.objects.create(name="Scoped tenant", slug="scoped-tenant")
        self.membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        self.token = Token.objects.create(user=self.user, tenant=self.tenant)
        self.factory = APIRequestFactory()
        self.auth = TokenAuthentication()

    def _request(self):
        return self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_authentication_populates_request_and_active_membership(self):
        request = self._request()
        user, returned_token = self.auth.authenticate(request)

        self.assertEqual(user.pk, self.user.pk)
        self.assertIsInstance(returned_token, Token)
        self.assertEqual(returned_token.pk, self.token.pk)
        self.assertEqual(request.active_tenant, self.tenant)
        self.assertEqual(request.active_membership, self.membership)
        self.assertIs(request.active_membership, get_current_membership())
        self.assertEqual(get_current_tenant(), self.tenant)
        self.assertIsNone(request.active_tenant_group)

    def test_authentication_replaces_all_accessible_with_token_tenant(self):
        set_current_all_accessible(True)

        request = self._request()
        self.auth.authenticate(request)

        self.assertFalse(get_current_all_accessible())
        self.assertEqual(get_current_tenant(), self.tenant)
        self.assertEqual(request.active_tenant, self.tenant)
        self.assertFalse(getattr(request, "active_all_accessible", False))

    def test_inactive_membership_revokes_token_authentication(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        assert_authentication_failure(
            self,
            self.auth,
            self._request(),
            "Token user no longer has access to the token tenant.",
        )

    def test_deleted_tenant_revokes_token_authentication(self):
        Tenant._base_manager.filter(pk=self.tenant.pk).update(deleted_at=timezone.now())

        assert_authentication_failure(
            self,
            self.auth,
            self._request(),
            "Token tenant inactive or deleted.",
        )

    def test_inactive_user_has_exact_failure_message(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        assert_authentication_failure(
            self,
            self.auth,
            self._request(),
            "User inactive or deleted.",
        )

    def test_deleted_tenant_check_precedes_lost_access_check(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        Tenant._base_manager.filter(pk=self.tenant.pk).update(deleted_at=timezone.now())

        assert_authentication_failure(
            self,
            self.auth,
            self._request(),
            "Token tenant inactive or deleted.",
        )

    def test_unknown_token_has_exact_failure_message(self):
        request = self.factory.get("/api/", HTTP_AUTHORIZATION="Token definitely-not-a-token")
        assert_authentication_failure(self, self.auth, request, "Invalid token.")

    def test_expired_token_has_exact_failure_message(self):
        expired_token = Token.objects.create(
            user=self.user,
            tenant=self.tenant,
            expires=timezone.now() - timezone.timedelta(seconds=1),
        )

        assert_authentication_failure(
            self,
            self.auth,
            self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {expired_token.key}"),
            "Token expired.",
        )

    def test_cold_last_used_is_written_and_warm_last_used_is_not_rewritten(self):
        token = Token.objects.create(user=self.user, tenant=self.tenant)
        self.assertIsNone(Token.objects.get(pk=token.pk).last_used)

        self.auth.authenticate(self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}"))
        cold_last_used = Token.objects.get(pk=token.pk).last_used
        self.assertIsNotNone(cold_last_used)

        warm_last_used = timezone.now()
        Token.objects.filter(pk=token.pk).update(last_used=warm_last_used)
        self.auth.authenticate(self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}"))
        self.assertEqual(Token.objects.get(pk=token.pk).last_used, warm_last_used)

    def test_warm_success_is_at_most_four_queries_and_preserves_result_identity(self):
        token = Token.objects.create(user=self.user, tenant=self.tenant, last_used=timezone.now())

        with CaptureQueriesContext(connection) as queries:
            user, returned_token = self.auth.authenticate(
                self.factory.get("/api/", HTTP_AUTHORIZATION=f"Token {token.key}")
            )

        self.assertLessEqual(len(queries), 4, queries.captured_queries)
        self.assertEqual(user.pk, self.user.pk)
        self.assertIsInstance(returned_token, Token)
        self.assertEqual(returned_token.pk, token.pk)
        self.assertEqual(returned_token.user_id, self.user.pk)
        self.assertEqual(returned_token.tenant_id, self.tenant.pk)
