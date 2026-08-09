import logging
import smtplib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from core.events import DeliveryDisposition, send_notification_to_channel
from core.tasks.webhooks import send_webhook_task


class DeliveryContractTests(SimpleTestCase):
    def test_webhook_5xx_is_retryable(self):
        response = MagicMock(status_code=503)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with (
            patch("core.http.request_pinned", return_value=response),
            patch("core.tasks.webhooks.async_task"),
        ):
            result = send_webhook_task(
                url="https://example.com/hook?token=secret-query",
                method="POST",
                headers={"Authorization": "Bearer secret-header"},
                secret="secret-signing-key",
                event_action="create",
                event_model_app_label="assets",
                event_model_name="asset",
                event_object_id=1,
                event_timestamp_iso="2026-01-01T00:00:00Z",
                event_data={"password": "secret-payload"},
                retry_count=1,
                retry_backoff=0,
            )

        self.assertEqual(result.disposition, DeliveryDisposition.RETRYABLE)
        self.assertFalse(result.user_visible)

    def test_webhook_4xx_is_terminal_and_safe_for_users(self):
        response = MagicMock(status_code=401)
        with patch("core.http.request_pinned", return_value=response):
            result = send_webhook_task(
                url="https://example.com/hook",
                method="POST",
                headers={},
                secret="",
                event_action="create",
                event_model_app_label="assets",
                event_model_name="asset",
                event_object_id=1,
                event_timestamp_iso="2026-01-01T00:00:00Z",
                event_data={},
            )

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        self.assertNotIn("401", result.user_message)

    def test_webhook_logs_redact_all_delivery_secrets(self):
        response = MagicMock(status_code=422)
        with (
            patch("core.http.request_pinned", return_value=response),
            self.assertLogs("core.tasks.webhooks", logging.WARNING) as captured,
        ):
            send_webhook_task(
                url="https://example.com/hook?token=secret-query",
                method="POST",
                headers={"Authorization": "Bearer secret-header"},
                secret="secret-signing-key",
                event_action="create",
                event_model_app_label="assets",
                event_model_name="asset",
                event_object_id=1,
                event_timestamp_iso="2026-01-01T00:00:00Z",
                event_data={"body": "secret-payload"},
                tenant_id=7,
                actor_id=11,
                request_id="correlation-13",
            )

        rendered = " ".join(captured.output)
        self.assertIn("operation=webhook.deliver", rendered)
        self.assertIn("actor_id=11", rendered)
        self.assertIn("tenant_id=7", rendered)
        self.assertIn("request_id=correlation-13", rendered)
        self.assertIn("endpoint=https://example.com", rendered)
        for secret in ("secret-query", "secret-header", "secret-signing-key", "secret-payload"):
            self.assertNotIn(secret, rendered)

    @patch("core.events.EmailSettings.load")
    @patch("core.events.get_connection")
    def test_smtp_authentication_failure_is_terminal_and_user_visible(self, get_connection, load_settings):
        load_settings.return_value = SimpleNamespace(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password_decrypted="secret-password",
            smtp_use_tls=True,
            from_name="ITAMbox",
            from_address="noreply@example.com",
        )
        connection = MagicMock()
        connection.send_messages.side_effect = smtplib.SMTPAuthenticationError(535, b"secret-provider-detail")
        get_connection.return_value = connection
        channel = SimpleNamespace(
            channel_type="email",
            config={"recipients": ["admin@example.com"]},
            name="Mail",
            tenant_id=7,
        )

        with self.assertLogs("core.events", logging.ERROR) as captured:
            result = send_notification_to_channel(channel, "secret-subject", "secret-mail-body")

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        rendered = " ".join(captured.output)
        self.assertIn("tenant_id=7", rendered)
        self.assertNotIn("secret-password", rendered)
        self.assertNotIn("secret-provider-detail", rendered)
        self.assertNotIn("secret-mail-body", rendered)
