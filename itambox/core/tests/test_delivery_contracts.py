import logging
import smtplib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import requests
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.events import (
    DeliveryDisposition,
    DeliveryResult,
    delivery_log_context,
    send_email_notification,
    send_notification_to_channel,
)
from extras.models import Event, WebhookDelivery, WebhookEndpoint
from extras.tasks.alerts import _dispatch_channels
from extras.tasks.webhooks import WebhookDeliveryAssertions, send_webhook_task
from organization.models import Tenant


class DeliveryContractTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Delivery Contract Tenant", slug="delivery-contract-tenant")
        self.tenant_id = self.tenant.pk

    def _webhook_delivery(
        self,
        *,
        url="https://example.com/hook",
        headers=None,
        secret="",
        retry_count=0,
        event_data=None,
    ):
        endpoint = WebhookEndpoint.objects.create(
            name=f"Delivery endpoint {uuid4()}",
            url=url,
            http_method=WebhookEndpoint.HTTP_POST,
            headers=headers or {},
            secret=secret,
            retry_count=retry_count,
            retry_backoff=0,
            tenant=self.tenant,
        )
        event = None
        test_send = event_data is None
        if not test_send:
            event = Event.objects.create(
                model=ContentType.objects.get_for_model(Tenant),
                object_id=self.tenant.pk,
                action=Event.ACTION_CREATE,
                data=event_data,
            )
        delivery_id = uuid4()
        delivery = WebhookDelivery.objects.create(
            tenant=self.tenant,
            endpoint=endpoint,
            event=event,
            payload_timestamp=event.timestamp if event is not None else timezone.now(),
            delivery_id=str(delivery_id),
            test_send=test_send,
            attempt=1,
            status=WebhookDelivery.STATUS_PENDING,
            target_url=endpoint.url,
            target_http_method=endpoint.http_method,
            target_headers=endpoint.headers,
            target_secret=endpoint.secret,
            target_enabled=endpoint.enabled and endpoint.deleted_at is None,
            target_tenant_id=endpoint.tenant_id,
            target_retry_count=endpoint.retry_count,
            target_retry_backoff=endpoint.retry_backoff,
        )
        assertions = WebhookDeliveryAssertions(
            delivery_pk=delivery.pk,
            delivery_id=UUID(str(delivery.delivery_id)),
            webhook_endpoint_id=endpoint.pk,
            event_id=event.pk if event is not None else None,
            tenant_id=self.tenant.pk,
            test_send=test_send,
        )
        return assertions

    def test_delivery_result_truth_value_preserves_success_contract(self):
        success = DeliveryResult("test.deliver", DeliveryDisposition.SUCCESS)
        retryable = DeliveryResult("test.deliver", DeliveryDisposition.RETRYABLE)
        terminal = DeliveryResult("test.deliver", DeliveryDisposition.TERMINAL, True, "safe message")

        self.assertTrue(success)
        self.assertFalse(retryable)
        self.assertFalse(terminal)

    def test_delivery_log_context_uses_ambient_values_and_redacts_endpoint(self):
        user = SimpleNamespace(pk=11)
        tenant = SimpleNamespace(pk=7)
        with (
            patch("core.events.get_current_user", return_value=user),
            patch("core.events.get_current_tenant", return_value=tenant),
            patch("core.events.get_current_request_id", return_value="request-13"),
        ):
            context = delivery_log_context("test.deliver", endpoint="https://example.com/hook?token=secret")

        self.assertEqual(context["actor_id"], 11)
        self.assertEqual(context["tenant_id"], 7)
        self.assertEqual(context["request_id"], "request-13")
        self.assertEqual(context["endpoint"], "https://example.com")
        self.assertNotIn("token", context["endpoint"])

        with_port = delivery_log_context("test.deliver", endpoint="https://example.com:8443/hook")
        self.assertEqual(with_port["endpoint"], "https://example.com:8443")

        malformed_port = delivery_log_context("test.deliver", endpoint="https://example.com:not-a-port/hook")
        self.assertEqual(malformed_port["endpoint"], "")

        explicit = delivery_log_context("test.deliver", tenant_id=17, actor_id=19, request_id="explicit")
        self.assertEqual(explicit["actor_id"], 19)
        self.assertEqual(explicit["tenant_id"], 17)
        self.assertEqual(explicit["request_id"], "explicit")
        self.assertEqual(explicit["endpoint"], "")

    def test_webhook_5xx_is_retryable(self):
        assertions = self._webhook_delivery(
            url="https://example.com/hook?token=secret-query",
            headers={"Authorization": "Bearer secret-header"},
            secret="secret-signing-key",
            retry_count=1,
        )
        response = MagicMock(status_code=503)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with (
            patch("core.http.request_pinned", return_value=response),
            patch("extras.tasks.webhooks.async_task"),
        ):
            result = send_webhook_task(assertions=assertions, attempt=0)

        self.assertEqual(result.disposition, DeliveryDisposition.RETRYABLE)
        self.assertFalse(result.user_visible)

    def test_webhook_4xx_is_terminal_and_safe_for_users(self):
        assertions = self._webhook_delivery()
        response = MagicMock(status_code=401)
        with patch("core.http.request_pinned", return_value=response):
            result = send_webhook_task(assertions=assertions, attempt=0)

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        self.assertNotIn("401", result.user_message)

    def test_webhook_logs_redact_all_delivery_secrets(self):
        assertions = self._webhook_delivery(
            url="https://example.com/hook?token=secret-query",
            headers={"Authorization": "Bearer secret-header"},
            secret="secret-signing-key",
            event_data={"body": "secret-payload"},
        )
        with (
            patch("core.http.request_pinned", side_effect=requests.ConnectionError("secret-raw-exception")),
            self.assertLogs("extras.tasks.webhooks", logging.WARNING) as captured,
        ):
            send_webhook_task(
                assertions=assertions,
                attempt=0,
                actor_id=11,
                request_id="correlation-13",
            )

        rendered = " ".join(captured.output)
        self.assertIn("operation=webhook.deliver", rendered)
        self.assertIn("actor_id=11", rendered)
        self.assertIn(f"tenant_id={self.tenant_id}", rendered)
        self.assertIn("request_id=correlation-13", rendered)
        self.assertIn("endpoint=https://example.com", rendered)
        for secret in (
            "secret-query",
            "secret-header",
            "secret-signing-key",
            "secret-payload",
            "secret-raw-exception",
        ):
            self.assertNotIn(secret, rendered)

    def test_webhook_blocked_target_is_terminal_and_user_visible(self):
        assertions = self._webhook_delivery(url="https://example.com/hook?token=secret-query")
        with patch("core.http.request_pinned", side_effect=ValidationError("secret internal target")) as request_pinned:
            result = send_webhook_task(assertions=assertions, attempt=0)

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        self.assertEqual(result.user_message, "Invalid webhook configuration.")
        self.assertNotIn("secret internal target", result.user_message)
        request_pinned.assert_called_once()

    def test_slack_and_teams_blocked_targets_are_terminal(self):
        for channel_type in ("slack", "teams"):
            with self.subTest(channel_type=channel_type):
                channel = SimpleNamespace(
                    channel_type=channel_type,
                    config={"webhook_url": "https://example.com/hook?token=secret-query"},
                    tenant_id=7,
                )
                with patch("core.http.request_pinned", side_effect=ValidationError("secret internal target")):
                    result = send_notification_to_channel(channel, "subject", "body")

                self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
                self.assertTrue(result.user_visible)
                self.assertNotIn("secret", result.user_message)

    def test_slack_and_teams_4xx_are_terminal(self):
        for channel_type in ("slack", "teams"):
            with self.subTest(channel_type=channel_type):
                channel = SimpleNamespace(
                    channel_type=channel_type,
                    config={"webhook_url": "https://example.com/hook"},
                    tenant_id=7,
                )
                response = MagicMock(status_code=429)
                with patch("core.http.request_pinned", return_value=response):
                    result = send_notification_to_channel(channel, "subject", "body")

                self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
                self.assertTrue(result.user_visible)
                response.raise_for_status.assert_not_called()

    def test_slack_without_title_sends_plain_text_payload(self):
        channel = SimpleNamespace(
            channel_type="slack",
            config={"webhook_url": "https://example.com/hook"},
            tenant_id=7,
        )
        response = MagicMock(status_code=200)
        with patch("core.http.request_pinned", return_value=response) as request_pinned:
            result = send_notification_to_channel(channel, "", "plain body")

        self.assertEqual(result.disposition, DeliveryDisposition.SUCCESS)
        self.assertEqual(request_pinned.call_args.kwargs["json"], {"text": "plain body"})

    def test_slack_and_teams_transport_failures_are_retryable(self):
        for channel_type in ("slack", "teams"):
            with self.subTest(channel_type=channel_type):
                channel = SimpleNamespace(
                    channel_type=channel_type,
                    config={"webhook_url": "https://example.com/hook"},
                    tenant_id=7,
                )
                with patch("core.http.request_pinned", side_effect=requests.ConnectionError("secret detail")):
                    result = send_notification_to_channel(channel, "subject", "body")

                self.assertEqual(result.disposition, DeliveryDisposition.RETRYABLE)
                self.assertFalse(result.user_visible)

    @patch("core.events.EmailSettings.load", return_value=None)
    def test_missing_email_configuration_is_terminal(self, load_settings):
        channel = SimpleNamespace(channel_type="email", config={"recipients": ["admin@example.com"]}, tenant_id=7)

        result = send_notification_to_channel(channel, "subject", "body")

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        self.assertIn("not configured", result.user_message)

    @patch("core.events.EmailSettings.load")
    def test_email_without_recipients_is_terminal(self, load_settings):
        load_settings.return_value = SimpleNamespace(enabled=True)
        channel = SimpleNamespace(channel_type="email", config={"recipients": []}, tenant_id=7)

        result = send_notification_to_channel(channel, "subject", "body")

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)
        self.assertIn("No email recipients", result.user_message)

    @patch("core.events.EmailSettings.load")
    @patch("core.events.get_connection")
    def test_email_success_and_failure_classification(self, get_connection, load_settings):
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
        channel = SimpleNamespace(channel_type="email", config={"recipients": ["admin@example.com"]}, tenant_id=7)
        connection = MagicMock()
        get_connection.return_value = connection

        expected = (
            (None, DeliveryDisposition.SUCCESS, False),
            (TimeoutError("secret timeout"), DeliveryDisposition.RETRYABLE, False),
            (smtplib.SMTPRecipientsRefused({}), DeliveryDisposition.TERMINAL, True),
        )
        for error, disposition, user_visible in expected:
            with self.subTest(error=type(error).__name__ if error else "success"):
                connection.send_messages.side_effect = error
                result = send_notification_to_channel(channel, "subject", "body")
                self.assertEqual(result.disposition, disposition)
                self.assertEqual(result.user_visible, user_visible)

    @patch("core.events.EmailSettings.load")
    @patch("core.events.get_connection")
    def test_recipient_aware_email_helper_sends_only_explicit_recipients(self, get_connection, load_settings):
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
        get_connection.return_value = connection

        result = send_email_notification(
            ["holder@example.test"],
            "Custody handoff",
            "one-time body",
            tenant_id=7,
        )

        self.assertEqual(result.disposition, DeliveryDisposition.SUCCESS)
        message = connection.send_messages.call_args.args[0][0]
        self.assertEqual(message.to, ["holder@example.test"])
        self.assertNotIn("test@example", message.to)

    @patch("core.events.EmailSettings.load")
    @patch("core.events.get_connection")
    def test_recipient_aware_email_helper_classifies_invalid_message_safely(self, get_connection, load_settings):
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
        with patch("core.events.EmailMessage", side_effect=ValueError("secret header detail")):
            result = send_email_notification(
                ["holder@example.test"],
                "subject",
                "body",
                tenant_id=7,
            )

        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertEqual(result.error_class, "invalid_message")
        self.assertNotIn("secret header detail", result.user_message)
        get_connection.assert_called_once()

    def test_unsupported_notification_channel_is_terminal(self):
        channel = SimpleNamespace(channel_type="webhook", config={}, tenant_id=7)

        result = send_notification_to_channel(channel, "subject", "body")

        self.assertEqual(result.operation, "channel.deliver")
        self.assertEqual(result.disposition, DeliveryDisposition.TERMINAL)
        self.assertTrue(result.user_visible)

    def test_alert_dispatch_records_no_channels_reason(self):
        queryset = MagicMock()
        queryset.exists.return_value = False
        manager = MagicMock()
        manager.all.return_value = queryset
        rule = SimpleNamespace(channels=manager, tenant_id=7)

        result = _dispatch_channels(rule, {"subject": "subject", "message": "body"}, None)

        self.assertEqual(result, {"__no_channels__": "no channels attached to this rule"})

    def test_alert_dispatch_isolates_unexpected_channel_exception(self):
        channel = SimpleNamespace(pk=23)
        queryset = MagicMock()
        queryset.exists.return_value = True
        queryset.__iter__.return_value = iter([channel])
        manager = MagicMock()
        manager.all.return_value = queryset
        rule = SimpleNamespace(channels=manager, tenant_id=7)

        with (
            patch("extras.tasks.alerts.send_notification_to_channel", side_effect=RuntimeError("secret detail")),
            self.assertLogs("extras.tasks.alerts", logging.ERROR) as captured,
        ):
            result = _dispatch_channels(rule, {"subject": "secret subject", "message": "secret body"}, None)

        self.assertEqual(result["23"]["disposition"], DeliveryDisposition.TERMINAL.value)
        self.assertEqual(result["23"]["error_class"], "unexpected_channel_error")
        rendered = " ".join(captured.output)
        self.assertIn("operation=alert.channel.dispatch", rendered)
        self.assertIn("channel_id=23", rendered)
        self.assertNotIn("secret detail", rendered)
        self.assertNotIn("secret body", rendered)

    def test_alert_dispatch_preserves_success_and_retryable_dispositions(self):
        channels = [SimpleNamespace(pk=23), SimpleNamespace(pk=29)]
        queryset = MagicMock()
        queryset.exists.return_value = True
        queryset.__iter__.return_value = iter(channels)
        manager = MagicMock()
        manager.all.return_value = queryset
        rule = SimpleNamespace(channels=manager, tenant_id=7)
        results = [
            DeliveryResult("channel.deliver", DeliveryDisposition.SUCCESS),
            DeliveryResult("channel.deliver", DeliveryDisposition.RETRYABLE),
        ]

        with patch("extras.tasks.alerts.send_notification_to_channel", side_effect=results):
            delivery = _dispatch_channels(rule, {"subject": "subject", "message": "body"}, None)

        self.assertEqual(delivery["23"]["disposition"], DeliveryDisposition.SUCCESS.value)
        self.assertEqual(delivery["29"]["disposition"], DeliveryDisposition.RETRYABLE.value)
        self.assertNotIn("error_class", delivery["23"])
        self.assertNotIn("message", delivery["29"])

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
