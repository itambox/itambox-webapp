from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase

from core.auth.ldap import MultiTenantLDAPBackend
from core.auth.oidc import TenantOIDCAuthorizeView, TenantOIDCCallbackView


class _FailingLDAPUser:
    @property
    def attrs(self):
        raise RuntimeError("provider attribute failure")

    @property
    def group_names(self):
        raise RuntimeError("provider group-name failure")

    @property
    def group_dns(self):
        raise RuntimeError("provider group-dn failure")


class LDAPBoundaryTests(SimpleTestCase):
    def test_unexpected_configuration_failure_propagates(self):
        backend = MultiTenantLDAPBackend()
        with patch.object(
            type(backend),
            "settings",
            new_callable=PropertyMock,
            side_effect=RuntimeError("unexpected failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                backend._is_configured()

    @patch("core.auth.provisioning.provision_membership")
    @patch("core.auth.ldap.get_current_tenant")
    def test_provider_attribute_and_group_failures_are_logged_with_context(self, current_tenant, provision):
        tenant = SimpleNamespace(pk=17, slug="ldap-tenant")
        current_tenant.return_value = tenant
        user = SimpleNamespace(
            pk=23,
            username="ldap-user",
            email="ldap@example.test",
            first_name="LDAP",
            last_name="User",
            ldap_user=_FailingLDAPUser(),
        )
        profiles = Mock()
        profiles.filter.return_value.first.return_value = SimpleNamespace(user=user)
        user.asset_holder_profiles = profiles

        with self.settings(ITAMBOX_TENANT_LDAP_CONFIGS={}):
            with self.assertLogs("django_auth_ldap", level="DEBUG") as logs:
                MultiTenantLDAPBackend().sync_ldap_user_profile_and_memberships(user)

        output = "\n".join(logs.output)
        self.assertIn("user_id=23", output)
        self.assertIn("tenant_id=17", output)
        provision.assert_called_once()


class OIDCStaleTenantTests(TestCase):
    def test_stale_authorize_session_tenant_is_removed(self):
        request = SimpleNamespace(GET={}, session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationRequestView.dispatch",
            return_value=HttpResponse(status=200),
        ):
            response = TenantOIDCAuthorizeView().dispatch(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("oidc_tenant_slug", request.session)

    def test_stale_callback_session_tenant_is_removed(self):
        request = SimpleNamespace(session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationCallbackView.dispatch",
            return_value=HttpResponse(status=200),
        ):
            response = TenantOIDCCallbackView().dispatch(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("oidc_tenant_slug", request.session)

    def test_stale_login_success_tenant_is_removed(self):
        view = TenantOIDCCallbackView()
        view.request = SimpleNamespace(session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationCallbackView.login_success",
            return_value=HttpResponse(status=302),
        ):
            response = view.login_success()

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("oidc_tenant_slug", view.request.session)
