from unittest import TestCase
from unittest.mock import MagicMock, patch

from core.errors import (
    FailureDisposition,
    IntegrationAuthenticationError,
    IntegrationContext,
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

    def test_optional_detected_apps_failure_preserves_asset_sync_degradation(self):
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

        self.assertEqual(result, 0)
        extra = log_warning.call_args.kwargs["extra"]["integration"]
        self.assertEqual(extra["operation"], "device_apps.list")
        self.assertEqual(extra["object_id"], "device-17")
        self.assertEqual(extra["disposition"], FailureDisposition.RETRYABLE.value)
