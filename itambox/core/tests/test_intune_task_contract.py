from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.test import override_settings

from core.errors import (
    FailureDisposition,
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationRateLimitedError,
    IntegrationUnavailableError,
)


class IntuneTaskBoundaryContractTests(TestCase):
    def setUp(self):
        self.context_manager = MagicMock()
        self.context_manager.__enter__.return_value.tenant = MagicMock(slug="tenant-a")
        self.context_manager.__exit__.return_value = False
        self.job = MagicMock()
        self.job.mark_running.return_value = True

    def _run_task_with_error(self, error):
        from core.tasks import intune_sync

        with (
            patch.object(intune_sync, "TaskContext", return_value=self.context_manager),
            patch.object(intune_sync.Job.objects, "get", return_value=self.job),
            patch.object(intune_sync, "_run_sync", side_effect=error),
            patch.object(intune_sync.logger, "error") as log_error,
        ):
            intune_sync.sync_tenant_intune(17, 23, 91)
        return log_error

    def test_terminal_provider_failure_is_user_visible_and_structured(self):
        error = IntegrationAuthenticationError(
            context=IntegrationContext(
                provider="microsoft-graph",
                operation="oauth.token",
                tenant_id=17,
                actor_id=23,
                request_id="request-123",
            ),
            status_code=401,
        )

        log_error = self._run_task_with_error(error)

        self.job.mark_failed.assert_called_once_with(error.user_message)
        self.assertIn("integration=%s", log_error.call_args.args[0])
        self.assertIn("code=integration.authentication", self.job.append_log.call_args.args[0])
        extra = log_error.call_args.kwargs["extra"]["integration"]
        self.assertEqual(extra["disposition"], FailureDisposition.TERMINAL.value)
        self.assertEqual(extra["tenant_id"], 17)
        self.assertEqual(extra["actor_id"], 23)
        self.assertEqual(extra["request_id"], "request-123")
        self.assertNotIn("401", self.job.mark_failed.call_args.args[0])

    def test_retryable_provider_failure_keeps_retry_classification(self):
        error = IntegrationUnavailableError(
            context=IntegrationContext(
                provider="microsoft-graph",
                operation="devices.list",
                tenant_id=17,
                actor_id=23,
                request_id="request-123",
            )
        )

        log_error = self._run_task_with_error(error)

        self.job.mark_failed.assert_called_once_with(error.user_message)
        self.assertEqual(
            log_error.call_args.kwargs["extra"]["integration"]["disposition"],
            FailureDisposition.RETRYABLE.value,
        )

    def test_rate_limited_internal_signal_gets_safe_user_fallback(self):
        error = IntegrationRateLimitedError(
            context=IntegrationContext(
                provider="microsoft-graph",
                operation="oauth.token",
                tenant_id=17,
                actor_id=23,
                request_id="request-123",
            ),
            status_code=429,
            retry_after=300,
        )

        log_error = self._run_task_with_error(error)

        self.job.mark_failed.assert_called_once()
        self.assertNotIn("rate-limited", self.job.mark_failed.call_args.args[0])
        self.assertEqual(
            log_error.call_args.kwargs["extra"]["integration"]["disposition"],
            FailureDisposition.RETRYABLE.value,
        )
        self.assertEqual(log_error.call_args.kwargs["extra"]["integration"]["retry_after"], 300.0)

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS={})
    def test_missing_tenant_configuration_is_terminal_and_typed(self):
        from core.tasks.intune_sync import _run_sync

        tenant = MagicMock(slug="missing", pk=17)
        with self.assertRaises(IntegrationConfigurationError) as raised:
            _run_sync(tenant, dry_run=True, job=MagicMock())

        self.assertEqual(raised.exception.disposition, FailureDisposition.TERMINAL)
        self.assertEqual(raised.exception.context.tenant_id, 17)

    @override_settings(ITAMBOX_TENANT_INTUNE_CONFIGS={"tenant-a": {"azure_tenant_id": "azure-tenant"}})
    def test_incomplete_tenant_configuration_is_terminal_and_typed(self):
        from core.tasks.intune_sync import _run_sync

        tenant = MagicMock(slug="tenant-a", pk=17)
        with self.assertRaises(IntegrationConfigurationError) as raised:
            _run_sync(tenant, dry_run=True, job=MagicMock())

        self.assertEqual(raised.exception.disposition, FailureDisposition.TERMINAL)
        self.assertEqual(raised.exception.context.tenant_id, 17)

    @override_settings(
        ITAMBOX_TENANT_INTUNE_CONFIGS={
            "tenant-a": {
                "azure_tenant_id": "azure-tenant",
                "client_id": "client-id",
                "client_secret": " ",
            }
        }
    )
    def test_blank_credential_configuration_is_terminal_and_typed(self):
        from core.tasks.intune_sync import _run_sync

        tenant = MagicMock(slug="tenant-a", pk=17)
        with self.assertRaises(IntegrationConfigurationError) as raised:
            _run_sync(tenant, dry_run=True, job=MagicMock())

        self.assertEqual(raised.exception.disposition, FailureDisposition.TERMINAL)

    def test_unknown_failure_is_not_persisted_or_logged_with_exception_text(self):
        from core.tasks import intune_sync

        with (
            patch.object(intune_sync, "TaskContext", return_value=self.context_manager),
            patch.object(intune_sync.Job.objects, "get", return_value=self.job),
            patch.object(intune_sync, "_run_sync", side_effect=RuntimeError("client_secret=do-not-log")),
            patch.object(intune_sync.logger, "error") as log_error,
        ):
            intune_sync.sync_tenant_intune(17, 23, 91)

        self.job.mark_failed.assert_called_once()
        self.assertNotIn("client_secret", self.job.mark_failed.call_args.args[0])
        self.assertNotIn("do-not-log", str(log_error.call_args))
        self.assertEqual(
            log_error.call_args.kwargs["extra"]["integration"]["exception_type"],
            "RuntimeError",
        )

    def test_optional_authentication_failure_is_not_silently_degraded(self):
        from core.tasks import intune_sync

        client = MagicMock()
        client.get_detected_apps.side_effect = IntegrationAuthenticationError(
            context=IntegrationContext(
                provider="microsoft-graph",
                operation="device_apps.list",
                tenant_id=17,
            ),
            status_code=403,
        )

        with self.assertRaises(IntegrationAuthenticationError):
            intune_sync._sync_device_software(client, {"id": "device-17"}, MagicMock(), dry_run=False)

    def test_retryable_optional_detected_apps_failure_is_reported_as_degradation(self):
        from core.tasks import intune_sync

        client = MagicMock()
        client.context = IntegrationContext(
            provider="microsoft-graph",
            operation="sync",
            tenant_id=17,
            actor_id=23,
            request_id="request-123",
        )
        client.get_detected_apps.side_effect = IntegrationUnavailableError(
            context=IntegrationContext(
                provider="microsoft-graph",
                operation="device_apps.list",
                tenant_id=17,
                actor_id=23,
                request_id="request-123",
            )
        )

        with patch.object(intune_sync.logger, "warning") as log_warning:
            result = intune_sync._sync_device_software(
                client,
                {"id": "device-17"},
                MagicMock(),
                dry_run=False,
            )

        self.assertEqual(result, (0, True))
        extra = log_warning.call_args.kwargs["extra"]["integration"]
        self.assertEqual(extra["operation"], "device_apps.list")
        self.assertEqual(extra["object_id"], "device-17")
        self.assertEqual(extra["disposition"], FailureDisposition.RETRYABLE.value)
