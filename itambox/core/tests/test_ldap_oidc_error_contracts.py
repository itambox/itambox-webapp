import logging
import uuid
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.auth.ldap import (
    LDAPAuthenticationError,
    LDAPConfigurationError,
    LDAPUnavailableError,
    MultiTenantLDAPBackend,
    classify_ldap_error,
)
from core.auth.oidc import (
    OIDCConfigurationError,
    OIDCTokenConfigurationError,
    OIDCTokenValidationError,
    TenantOIDCBackend,
)
from core.errors import FailureDisposition, IntegrationContext
from core.management.commands.sync_tenant_ldap import Command as LDAPSyncCommand
from core.managers import set_current_tenant
from core.tasks.ldap import sync_tenant_ldap_task


class _TransientLDAPError(Exception):
    pass


class _InvalidCredentialsLDAPError(Exception):
    pass


class IntegrationContractTests(SimpleTestCase):
    def tearDown(self):
        set_current_tenant(None)

    def test_ldap_classifier_uses_provider_semantics_and_redacts_cause(self):
        context = IntegrationContext(
            provider="ldap",
            operation="sync.bind",
            tenant_id=17,
            actor_id=23,
            request_id="request-42",
        )
        secret = "bind-password=super-secret"

        with (
            patch("core.auth.ldap._LDAP_TRANSIENT_ERRORS", (_TransientLDAPError,)),
            patch("core.auth.ldap._LDAP_AUTHENTICATION_ERRORS", (_InvalidCredentialsLDAPError,)),
        ):
            transient_cause = _TransientLDAPError(secret)
            transient = classify_ldap_error(transient_cause, context=context)
            authentication = classify_ldap_error(_InvalidCredentialsLDAPError(secret), context=context)

        self.assertIsInstance(transient, LDAPUnavailableError)
        self.assertIsInstance(transient, CommandError)
        self.assertEqual(transient.disposition, FailureDisposition.RETRYABLE)
        self.assertIs(transient.__cause__, transient_cause)
        self.assertIsInstance(authentication, LDAPAuthenticationError)
        self.assertEqual(authentication.disposition, FailureDisposition.TERMINAL)
        self.assertNotIn(secret, str(transient))
        self.assertNotIn(secret, str(authentication))
        self.assertEqual(
            transient.log_extra()["integration"],
            {
                "provider": "ldap",
                "operation": "sync.bind",
                "tenant_id": 17,
                "actor_id": 23,
                "request_id": "request-42",
                "error_code": "ldap.unavailable",
                "disposition": "retryable",
                "retry_exhausted": False,
                "status_code": None,
                "cause_type": "_TransientLDAPError",
            },
        )

    def test_ldap_configuration_contract_is_terminal_and_command_compatible(self):
        error = LDAPConfigurationError(
            context=IntegrationContext(provider="ldap", operation="sync.configure", tenant_id=17)
        )

        self.assertIsInstance(error, CommandError)
        self.assertEqual(error.disposition, FailureDisposition.TERMINAL)
        self.assertEqual(error.display_message(), "LDAP configuration is incomplete or invalid.")

    @patch("core.auth.ldap.django_auth_ldap_installed", False)
    @patch("core.auth.ldap.LDAPBackend.authenticate")
    def test_missing_optional_ldap_dependency_fails_closed(self, parent_authenticate):
        backend = MultiTenantLDAPBackend()

        self.assertIsNone(backend.authenticate(request=None, username="user@example.com", password="secret"))
        parent_authenticate.assert_not_called()

    def test_oidc_configuration_error_preserves_django_contract(self):
        tenant = SimpleNamespace(pk=17, slug="tenant-alpha")
        set_current_tenant(tenant)

        with patch("core.auth.oidc.import_from_settings", side_effect=ImproperlyConfigured("client-secret-value")):
            with self.assertRaises(OIDCConfigurationError) as raised:
                TenantOIDCBackend.get_settings("OIDC_RP_CLIENT_ID")

        self.assertIsInstance(raised.exception, ImproperlyConfigured)
        self.assertIsInstance(raised.exception.__cause__, ImproperlyConfigured)
        self.assertNotIn("client-secret-value", str(raised.exception))
        self.assertEqual(raised.exception.context.tenant_id, 17)

    @patch("core.auth.oidc.OIDCAuthenticationBackend.verify_token")
    def test_oidc_token_validation_is_typed_structured_and_never_logs_token_or_claims(self, parent_verify):
        parent_verify.return_value = {
            "aud": "wrong-client",
            "iss": "https://issuer.example/",
            "email": "sensitive@example.com",
        }
        tenant = SimpleNamespace(pk=17, slug="tenant-alpha")
        set_current_tenant(tenant)
        backend = TenantOIDCBackend()
        token = "secret-id-token"

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        logger = logging.getLogger("core.auth.oidc")
        logger.addHandler(handler)
        try:
            with patch.object(backend, "get_settings", return_value="expected-client"):
                with self.assertRaises(OIDCTokenValidationError) as raised:
                    backend.verify_token(token)
        finally:
            logger.removeHandler(handler)

        self.assertIsInstance(raised.exception, SuspiciousOperation)
        self.assertEqual(raised.exception.code, "oidc.token_validation")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].integration["operation"], "token.verify")
        self.assertEqual(records[0].integration["tenant_id"], 17)
        rendered = f"{records[0].getMessage()} {records[0].integration}"
        self.assertNotIn(token, rendered)
        self.assertNotIn("sensitive@example.com", rendered)

    @patch("core.auth.oidc.OIDCAuthenticationBackend.verify_token")
    def test_missing_oidc_issuer_remains_a_suspicious_terminal_configuration_failure(self, parent_verify):
        parent_verify.return_value = {"aud": "expected-client", "iss": "https://unexpected.example/"}
        backend = TenantOIDCBackend()

        def settings_value(name, default=None):
            return "expected-client" if name == "OIDC_RP_CLIENT_ID" else default

        with patch.object(backend, "get_settings", side_effect=settings_value):
            with self.assertRaises(OIDCTokenConfigurationError) as raised:
                backend.verify_token("secret-id-token")

        self.assertIsInstance(raised.exception, SuspiciousOperation)
        self.assertEqual(raised.exception.disposition, FailureDisposition.TERMINAL)

    @patch("core.management.commands.sync_tenant_ldap.django_auth_ldap_installed", True)
    @patch("core.management.commands.sync_tenant_ldap.ldap.initialize")
    def test_sync_command_maps_transient_bind_failure_without_leaking_provider_text(self, initialize):
        connection = initialize.return_value
        secret = "bind-password=super-secret"
        connection.simple_bind_s.side_effect = _TransientLDAPError(secret)
        tenant = SimpleNamespace(pk=17, slug="tenant-alpha", name="Tenant Alpha")
        config = {
            tenant.slug: {
                "SERVER_URI": "ldap://directory.internal",
                "BIND_DN": "cn=service",
                "BIND_PASSWORD": secret,
                "USER_SEARCH_BASE": "ou=users,dc=internal",
            }
        }
        command = LDAPSyncCommand(stdout=StringIO())

        with (
            patch("core.management.commands.sync_tenant_ldap._LDAP_PROVIDER_ERROR", _TransientLDAPError),
            patch("core.auth.ldap._LDAP_TRANSIENT_ERRORS", (_TransientLDAPError,)),
            self.settings(ITAMBOX_TENANT_LDAP_CONFIGS=config),
        ):
            with self.assertRaises(LDAPUnavailableError) as raised:
                command._run_sync(tenant)

        self.assertEqual(raised.exception.context.operation, "sync.bind")
        self.assertEqual(raised.exception.context.tenant_id, 17)
        self.assertNotIn(secret, str(raised.exception))

    def test_sync_command_configuration_failure_is_typed(self):
        tenant = SimpleNamespace(pk=17, slug="tenant-alpha", name="Tenant Alpha")
        command = LDAPSyncCommand(stdout=StringIO())

        with self.settings(ITAMBOX_TENANT_LDAP_CONFIGS={}):
            with self.assertRaises(LDAPConfigurationError) as raised:
                command._run_sync(tenant)

        self.assertEqual(raised.exception.context.operation, "sync.configure")
        self.assertEqual(raised.exception.context.tenant_id, 17)

    @patch("core.tasks.ldap.Notification.objects.create")
    @patch("core.tasks.ldap.Job.objects.get")
    @patch("core.tasks.ldap.call_command")
    @patch("core.tasks.ldap.TaskContext")
    def test_task_records_retryable_contract_and_safe_message(
        self, task_context, call_command, get_job, create_notification
    ):
        request_id = uuid.uuid4()
        context = IntegrationContext(
            provider="ldap",
            operation="sync.bind",
            tenant_id=17,
            actor_id=23,
            request_id=str(request_id),
        )
        provider_secret = "diagnostic includes bind-password"
        cause = _TransientLDAPError(provider_secret)
        error = LDAPUnavailableError(context=context, cause_type=type(cause).__name__)
        error.__cause__ = cause
        call_command.side_effect = error
        job = MagicMock(pk=31)
        job.mark_running.return_value = True
        get_job.return_value = job
        task_context.return_value.__enter__.return_value = SimpleNamespace(user=SimpleNamespace(pk=23))

        sync_tenant_ldap_task(31, "tenant-alpha", 23, tenant_id=17)

        job.mark_failed.assert_called_once_with(error.display_message())
        persisted = " ".join(call.args[0] for call in job.append_log.call_args_list)
        self.assertIn("disposition=retryable", persisted)
        self.assertIn("operation=sync.bind", persisted)
        self.assertNotIn(provider_secret, persisted)
        notification_message = str(create_notification.call_args.kwargs["message"])
        self.assertIn(error.display_message(), notification_message)
        self.assertNotIn(provider_secret, notification_message)
