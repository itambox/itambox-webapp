"""send_webhook_task retry behaviour on the durable-delivery model (issue #445)."""

import ast
import datetime
import json
import uuid
from email.utils import format_datetime
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
            test_send=True,
            payload_timestamp=timezone.now(),
            attempt=1,
            status=WebhookDelivery.STATUS_PENDING,
            target_url=endpoint.url,
            target_http_method=endpoint.http_method,
            target_headers=endpoint.headers,
            target_secret=endpoint.secret,
            target_enabled=True,
            target_tenant_id=endpoint.tenant_id,
            target_retry_count=endpoint.retry_count,
            target_retry_backoff=endpoint.retry_backoff,
        )
        assertions = WebhookDeliveryAssertions(
            delivery_pk=delivery.pk,
            delivery_id=UUID(str(delivery.delivery_id)),
            webhook_endpoint_id=endpoint.pk,
            event_id=None,
            tenant_id=None,
            test_send=True,
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

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_called_once()
        _, kw = mock_async.call_args
        self.assertEqual(kw["attempt"], 1)
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

        send_webhook_task(assertions=assertions, attempt=0)
        retry_kwargs = mock_async.call_args.kwargs
        self.assertEqual(retry_kwargs["attempt"], 1)
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

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_2xx_no_retry(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan()
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

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

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertEqual(kw["func"], "extras.tasks.webhooks.send_webhook_task")
        self.assertEqual(kw["schedule_type"], mock_schedule.ONCE)
        self.assertGreater(kw["next_run"], timezone.now())
        retry = ast.literal_eval(kw["kwargs"])
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)
        self.assertNotIn("url", retry["assertions"])
        self.assertNotIn("secret", retry["assertions"])

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_endpoint_secret_not_persisted_in_retry_schedule(self, mock_schedule, mock_async, mock_request_pinned):
        """WS5-4: a retry must read encrypted durable state, never persist its secret
        in Schedule.kwargs (which django-q stores plaintext)."""
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(secret="top-secret", retry_backoff=60)
        resp = MagicMock(status_code=503)
        resp.raise_for_status.side_effect = __import__("requests").HTTPError(response=resp)
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        # The HMAC was still computed from the encrypted delivery snapshot.
        self.assertIn("X-Hub-Signature-256", mock_request_pinned.call_args[1]["headers"])
        # The retry Schedule.kwargs must NOT contain the secret — only identity claims.
        mock_schedule.objects.create.assert_called_once()
        _, kw = mock_schedule.objects.create.call_args
        self.assertNotIn("top-secret", kw["kwargs"])
        retry = ast.literal_eval(kw["kwargs"])
        self.assertEqual(retry["assertions"]["webhook_endpoint_id"], delivery.endpoint_id)
        self.assertNotIn("secret", retry["assertions"])

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_retries(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=60)
        resp = MagicMock(status_code=429)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_with_retry_after_seconds_schedules_delay(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=60)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "30"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp
        now = timezone.now()

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        next_run = mock_schedule.objects.create.call_args.kwargs["next_run"]
        self.assertGreaterEqual(next_run, now + datetime.timedelta(seconds=29))
        self.assertLessEqual(next_run, now + datetime.timedelta(seconds=32))
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_with_retry_after_seconds_schedules_delay_even_with_zero_endpoint_backoff(
        self, mock_schedule, mock_async, mock_request_pinned
    ):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=0)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "30"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp
        now = timezone.now()

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        next_run = mock_schedule.objects.create.call_args.kwargs["next_run"]
        self.assertGreaterEqual(next_run, now + datetime.timedelta(seconds=29))
        self.assertLessEqual(next_run, now + datetime.timedelta(seconds=32))
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_with_http_date_retry_after_schedules(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=0)
        now = timezone.now()
        retry_at = (now + datetime.timedelta(seconds=120)).astimezone(datetime.timezone.utc).replace(microsecond=0)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": format_datetime(retry_at, usegmt=True)}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        next_run = mock_schedule.objects.create.call_args.kwargs["next_run"]
        self.assertGreaterEqual(next_run, now + datetime.timedelta(seconds=118))
        self.assertLessEqual(next_run, now + datetime.timedelta(seconds=122))
        self.assertEqual(next_run.utcoffset(), datetime.timedelta(0))
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_retry_after_is_clamped_to_300_seconds(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=0)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "99999"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp
        now = timezone.now()

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        next_run = mock_schedule.objects.create.call_args.kwargs["next_run"]
        self.assertGreaterEqual(next_run, now + datetime.timedelta(seconds=299))
        self.assertLessEqual(next_run, now + datetime.timedelta(seconds=302))
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_invalid_retry_after_falls_back_to_backoff(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=60)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "not-a-number"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp
        now = timezone.now()

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_called_once()
        next_run = mock_schedule.objects.create.call_args.kwargs["next_run"]
        self.assertGreaterEqual(next_run, now + datetime.timedelta(seconds=47))
        self.assertLessEqual(next_run, now + datetime.timedelta(seconds=73))
        retry = ast.literal_eval(mock_schedule.objects.create.call_args.kwargs["kwargs"])
        self.assertEqual(retry["assertions"]["delivery_pk"], delivery.pk)

        mock_schedule.reset_mock()
        mock_async.reset_mock()
        delivery, assertions = self._plan(retry_backoff=0)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "not-a-number"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_schedule.objects.create.assert_not_called()
        mock_async.assert_called_once()
        self.assertEqual(mock_async.call_args.kwargs["attempt"], 1)
        delivery.refresh_from_db()
        self.assertLessEqual(delivery.next_retry_at, timezone.now())

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_429_without_retry_after_zero_backoff_retries_immediately(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=0)
        resp = MagicMock(status_code=429)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_called_once()
        self.assertEqual(mock_async.call_args.kwargs["attempt"], 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertLessEqual(delivery.next_retry_at, timezone.now())

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_with_retry_after_zero_retries_immediately(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=60)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "0"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_schedule.objects.create.assert_not_called()
        mock_async.assert_called_once()
        self.assertEqual(mock_async.call_args.kwargs["attempt"], 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertEqual(delivery.error_class, "integration.rate_limited")
        self.assertLessEqual(delivery.next_retry_at, timezone.now())

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_3xx_redirects_are_terminal_never_success(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        for status_code in (301, 302, 307):
            with self.subTest(status_code=status_code):
                delivery, assertions = self._plan()
                resp = MagicMock(status_code=status_code)
                resp.raise_for_status.return_value = None
                mock_request_pinned.return_value = resp

                send_webhook_task(assertions=assertions, attempt=0)

                mock_async.assert_not_called()
                delivery.refresh_from_db()
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                self.assertEqual(delivery.response_code, status_code)
                self.assertEqual(delivery.error_class, "integration.request_rejected")
                self.assertIsNone(delivery.next_retry_at)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    @patch("extras.tasks.webhooks.Schedule")
    def test_429_budget_exhausted_goes_dead(self, mock_schedule, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_count=2, retry_backoff=60)
        resp = MagicMock(status_code=429)
        resp.headers = {"Retry-After": "30"}
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=2)

        mock_async.assert_not_called()
        mock_schedule.objects.create.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
        self.assertEqual(delivery.error_class, "integration.retry_budget_exhausted")
        self.assertIsNone(delivery.next_retry_at)

    @patch("core.http.request_pinned")
    @patch("extras.tasks.webhooks.async_task")
    def test_429_records_rate_limited_error_class(self, mock_async, mock_request_pinned):
        from extras.tasks.webhooks import send_webhook_task

        delivery, assertions = self._plan(retry_backoff=0)
        resp = MagicMock(status_code=429)
        resp.raise_for_status.return_value = None
        mock_request_pinned.return_value = resp

        send_webhook_task(assertions=assertions, attempt=0)

        mock_async.assert_called_once()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertEqual(delivery.error_class, "integration.rate_limited")
