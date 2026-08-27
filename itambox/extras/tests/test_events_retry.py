"""send_webhook_task retry behaviour on the durable-delivery model (issue #445)."""

import ast
import json
import uuid
from unittest.mock import MagicMock, patch
from uuid import UUID

from django.test import TransactionTestCase
from django.utils import timezone

from extras.models import WebhookDelivery, WebhookEndpoint
from extras.tasks.webhooks import WebhookDeliveryAssertions


class WebhookRetryTestCase(TransactionTestCase):
    """send_webhook_task retry behaviour."""

    def _plan(self, *, retry_count=2, retry_backoff=0, secret=""):
        endpoint = WebhookEndpoint.objects.create(
            name="WH",
            url="https://example.com/hook",
            http_method="POST",
            headers={},
            secret=secret,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
        )
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            delivery_id=str(uuid.uuid4()),
            event_id=None,
            tenant_id=None,
            test_send=False,
            attempt=1,
            status=WebhookDelivery.STATUS_PENDING,
        )
        assertions = WebhookDeliveryAssertions(
            delivery_pk=delivery.pk,
            delivery_id=UUID(str(delivery.delivery_id)),
            webhook_endpoint_id=endpoint.pk,
            event_id=None,
            tenant_id=None,
            test_send=False,
        )
        return delivery, assertions

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_5xx_retries(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan()
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__("requests").HTTPError(response=resp)
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=1)

        mock_async.assert_called_once()
        _, kw = mock_async.call_args
        self.assertEqual(kw["attempt"], 2)
        self.assertEqual(kw["assertions"]["delivery_pk"], delivery.pk)
        self.assertNotIn("url", kw["assertions"])
        self.assertNotIn("secret", kw["assertions"])

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_retry_preserves_event_and_delivery_identity_and_advances_attempt(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_count=3)
        failed_response = MagicMock(status_code=503)
        failed_response.raise_for_status.side_effect = __import__("requests").HTTPError(response=failed_response)
        successful_response = MagicMock(status_code=200)
        successful_response.raise_for_status.return_value = None
        mock_request_pinned.side_effect = [failed_response, successful_response]

        send_webhook_task(assertions=assertions, attempt=1)
        retry_kwargs = mock_async.call_args.kwargs
        self.assertEqual(retry_kwargs["attempt"], 2)
        send_webhook_task(**retry_kwargs)

        first_payload = mock_request_pinned.call_args_list[0].kwargs["data"]
        second_payload = mock_request_pinned.call_args_list[1].kwargs["data"]
        first_payload = json.loads(first_payload)
        second_payload = json.loads(second_payload)
        self.assertEqual(first_payload["event_id"], second_payload["event_id"])
        self.assertEqual(first_payload["delivery_id"], second_payload["delivery_id"])
        self.assertEqual(first_payload["attempt"], 1)
        self.assertEqual(second_payload["attempt"], 2)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_5xx_gives_up_after_max_attempts(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_count=2)
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__("requests").HTTPError(response=resp)
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=2)

        mock_async.assert_not_called()

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_4xx_does_not_retry(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan()
        resp = MagicMock(status_code=422)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=1)

        mock_async.assert_not_called()

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_2xx_no_retry(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan()
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=1)

        mock_async.assert_not_called()

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_5xx_with_backoff_schedules_delayed_retry(self, mock_schedule, mock_async, mock_request_pinned):
        """A positive retry_backoff must defer the retry via a one-off Schedule,
        not re-enqueue immediately. The kwargs must round-trip through the same
        ast.literal_eval the django-q2 scheduler uses."""
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=60)
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__("requests").HTTPError(response=resp)
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=1)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertEqual(kw["func"], "extras.tasks.webhooks.send_webhook_task")
        self.assertEqual(kw["schedule_type"], mock_schedule.ONCE)
        self.assertGreater(kw["next_run"], timezone.now())
        retry = ast.literal_eval(kw["kwargs"])
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)
        self.assertNotIn("url", retry["assertions"])
        self.assertNotIn("secret", retry["assertions"])

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_endpoint_secret_not_persisted_in_retry_schedule(self, mock_schedule, mock_async, mock_request_pinned):
        """WS5-4: an endpoint-linked retry must re-derive the secret from the endpoint, never
        write it into Schedule.kwargs (which django-q stores plaintext)."""
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(secret="top-secret", retry_backoff=60)
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__("requests").HTTPError(response=resp)
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=1)

        # The HMAC was still computed (secret re-derived from the endpoint at run time).
        self.assertIn("X-Hub-Signature-256", mock_request_pinned.call_args[1]["headers"])
        # The retry Schedule.kwargs must NOT contain the secret — only identity claims.
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertNotIn("top-secret", kw["kwargs"])
        retry = ast.literal_eval(kw["kwargs"])
        self.assertEqual(retry["assertions"]["webhook_endpoint_id"], delivery.endpoint_id)
        self.assertNotIn("secret", retry["assertions"])
