from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.http import Http404, HttpResponse
from django.test import SimpleTestCase, TestCase

from core import identity_provisioning
from core.auth import ldap as ldap_module
from core.auth.oidc import TenantOIDCAuthorizeView, TenantOIDCCallbackView
from core.context import (
    get_current_tenant,
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)


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


class _CapturingIdentityProvisioner:
    def __init__(self):
        self.commands = []

    def provision(self, command):
        self.commands.append(command)
        return identity_provisioning.ExternalIdentityProvisioningResult(mode="customer")


class LDAPBoundaryTests(SimpleTestCase):
    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)

    def test_unexpected_configuration_failure_propagates(self):
        backend = ldap_module.MultiTenantLDAPBackend()
        with patch.object(
            type(backend),
            "settings",
            new_callable=PropertyMock,
            side_effect=RuntimeError("unexpected failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                backend._is_configured()

    def test_provider_attribute_and_group_failures_are_logged_with_context(self):
        tenant = SimpleNamespace(pk=17, slug="ldap-tenant")
        set_current_tenant(tenant)
        user = SimpleNamespace(
            pk=23,
            username="ldap-user",
            email="ldap@example.test",
            first_name="LDAP",
            last_name="User",
            ldap_user=_FailingLDAPUser(),
        )
        provider = _CapturingIdentityProvisioner()

        with (
            self.settings(
                ITAMBOX_TENANT_LDAP_CONFIGS={
                    tenant.slug: {
                        "USER_SEARCH": {
                            "base_dn": "ou=users,dc=example,dc=test",
                            "filter": "(uid=%(user)s)",
                        }
                    }
                }
            ),
            identity_provisioning.override_identity_provisioner(provider),
            patch.object(ldap_module, "django_auth_ldap_installed", True),
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=user) as parent_authenticate,
            self.assertLogs("django_auth_ldap", level="DEBUG") as logs,
        ):
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=user.username,
                password="directory-password",
            )

        self.assertIs(result, user)
        parent_authenticate.assert_called_once()
        self.assertEqual(len(provider.commands), 1)
        self.assertEqual(provider.commands[0].profile.email, "ldap@example.test")
        self.assertEqual(provider.commands[0].customer_role_name, "Member")
        records_by_reason = {record.reason_code: record for record in logs.records}
        self.assertEqual(records_by_reason["attribute_read_failed"].user_id, 23)
        self.assertEqual(records_by_reason["attribute_read_failed"].tenant_id, 17)
        self.assertEqual(records_by_reason["group_names_unavailable"].exception_type, "RuntimeError")
        self.assertEqual(records_by_reason["group_read_failed"].exception_type, "RuntimeError")
        output = "\n".join(logs.output)
        self.assertNotIn("provider attribute failure", output)
        self.assertNotIn("provider group-name failure", output)
        self.assertNotIn("provider group-dn failure", output)
        self.assertIs(get_current_tenant(), tenant)


class OIDCStaleTenantTests(TestCase):
    def test_stale_authorize_session_tenant_is_removed(self):
        request = SimpleNamespace(GET={}, session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationRequestView.dispatch",
            return_value=HttpResponse(status=200),
        ) as parent_dispatch:
            with self.assertRaises(Http404):
                TenantOIDCAuthorizeView().dispatch(request)

        parent_dispatch.assert_not_called()
        self.assertNotIn("oidc_tenant_slug", request.session)

    def test_stale_callback_session_tenant_is_removed(self):
        request = SimpleNamespace(session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationCallbackView.dispatch",
            return_value=HttpResponse(status=200),
        ) as parent_dispatch:
            with self.assertRaises(Http404):
                TenantOIDCCallbackView().dispatch(request)

        parent_dispatch.assert_not_called()
        self.assertNotIn("oidc_tenant_slug", request.session)

    def test_stale_login_success_tenant_is_removed(self):
        view = TenantOIDCCallbackView()
        view.request = SimpleNamespace(session={"oidc_tenant_slug": "missing"})
        with patch(
            "mozilla_django_oidc.views.OIDCAuthenticationCallbackView.login_success",
            return_value=HttpResponse(status=302),
        ) as parent_login_success:
            with self.assertRaises(Http404):
                view.login_success()

        parent_login_success.assert_not_called()
        self.assertNotIn("oidc_tenant_slug", view.request.session)
