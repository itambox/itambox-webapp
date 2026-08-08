import uuid
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
    IntegrationUntrustedNextLinkError,
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
        self.assertIn("status_code=401", self.job.append_log.call_args.args[0])
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

    def test_task_propagates_actor_tenant_and_request_context(self):
        from core.tasks import intune_sync

        request_id = uuid.uuid4()
        with (
            patch.object(intune_sync, "TaskContext", return_value=self.context_manager),
            patch.object(intune_sync.Job.objects, "get", return_value=self.job),
            patch.object(intune_sync, "_run_sync", return_value={}) as run_sync,
            patch.object(intune_sync, "get_current_request_id", return_value=request_id),
        ):
            intune_sync.sync_tenant_intune(17, 23, 91)

        context = run_sync.call_args.args[3]
        self.assertEqual(context.provider, "microsoft-graph")
        self.assertEqual(context.operation, "sync")
        self.assertEqual(context.tenant_id, 17)
        self.assertEqual(context.actor_id, 23)
        self.assertEqual(context.request_id, str(request_id))

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

    def test_optional_configuration_and_untrusted_link_failures_are_not_degraded(self):
        from core.tasks import intune_sync

        errors = (
            IntegrationConfigurationError(
                context=IntegrationContext(
                    provider="microsoft-graph",
                    operation="device_apps.list",
                    tenant_id=17,
                )
            ),
            IntegrationUntrustedNextLinkError(
                context=IntegrationContext(
                    provider="microsoft-graph",
                    operation="device_apps.list",
                    tenant_id=17,
                )
            ),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                client = MagicMock()
                client.get_detected_apps.side_effect = error
                with self.assertRaises(type(error)):
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

    def test_persistence_degradation_is_counted_and_redacted(self):
        from core.tasks import intune_sync

        client = MagicMock()
        client.context = IntegrationContext(
            provider="microsoft-graph",
            operation="sync",
            tenant_id=17,
            actor_id=23,
            request_id="request-123",
        )
        client.get_detected_apps.return_value = [
            {"displayName": "Sensitive App", "publisher": "Publisher", "version": "1"}
        ]

        with (
            patch("assets.models.Manufacturer") as manufacturer_model,
            patch("software.models.Software") as software_model,
            patch("software.models.InstalledSoftware") as installed_model,
            patch.object(intune_sync.logger, "warning") as log_warning,
        ):
            manufacturer_model.objects.get_or_create.return_value = (MagicMock(), False)
            software_model.objects.get_or_create.return_value = (MagicMock(), False)
            installed_model.objects.update_or_create.side_effect = RuntimeError("client_secret=do-not-log")

            result = intune_sync._sync_device_software(
                client,
                {"id": "device-17"},
                MagicMock(),
                dry_run=False,
            )

        self.assertEqual(result, (0, True))
        self.assertNotIn("client_secret", str(log_warning.call_args))
        self.assertNotIn("Sensitive App", str(log_warning.call_args))
        self.assertEqual(
            log_warning.call_args.kwargs["extra"]["integration"]["operation"],
            "device_apps.persist",
        )

    @override_settings(
        ITAMBOX_TENANT_INTUNE_CONFIGS={
            "tenant-a": {
                "azure_tenant_id": "azure-tenant",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "sync_software": True,
            }
        }
    )
    def test_run_sync_persists_nonzero_software_degradation(self):
        from core.tasks import intune_sync

        tenant = MagicMock(slug="tenant-a", pk=17)
        job = MagicMock()
        asset = MagicMock(custom_field_data={})
        asset.objects = MagicMock()

        with (
            patch("assets.models.Asset") as asset_model,
            patch("core.tasks.intune_sync.IntuneClient") as client_model,
            patch.object(intune_sync, "_sync_device_software", return_value=(0, True)),
        ):
            asset_model.objects.filter.return_value.select_related.return_value.first.return_value = asset
            client_model.return_value.get_managed_devices.return_value = [
                {"id": "device-17", "serialNumber": "serial-17"}
            ]

            result = intune_sync._run_sync(
                tenant,
                dry_run=True,
                job=job,
                integration_context=IntegrationContext(
                    provider="microsoft-graph",
                    operation="sync",
                    tenant_id=17,
                    actor_id=23,
                    request_id="request-123",
                ),
            )

        self.assertEqual(result["software_degraded"], 1)
        self.assertTrue(any("software_degraded=1" in call.args[0] for call in job.append_log.call_args_list))
