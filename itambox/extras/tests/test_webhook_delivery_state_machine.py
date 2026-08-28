import ast
import importlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations.special import RunPython
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone
from django_q.models import Schedule

from assets.models import Manufacturer
from core.events import DeliveryDisposition, DeliveryResult
from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import TenantTestMixin, grant
from extras.models import Event, EventRule, WebhookDelivery, WebhookEndpoint
from extras.services.events import process_event_rules
from extras.tasks.webhooks import (
    WebhookDeliveryAssertions,
    _decrypt_target_secret,
    _encrypted_secret_snapshot,
    _endpoint_target_snapshot,
    _finish_delivery,
    redeliver_webhook_delivery,
    send_webhook_task,
    send_webhook_test,
)
from organization.models import Role, Tenant

User = get_user_model()


class WebhookDeliveryStateMachineTests(TenantTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="Webhook Delivery Tenant", slug="webhook-delivery-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Delivery Tenant", slug="other-delivery-tenant")
        self.actor = User.objects.create_superuser(
            username="webhook-delivery-admin",
            email="webhook-delivery-admin@example.com",
            password="password",
        )
        self.endpoint = WebhookEndpoint._base_manager.create(
            name="Durable webhook",
            url="http://8.8.8.8/durable-hook",
            tenant=self.tenant,
            secret="signing-secret",
            retry_count=2,
            retry_backoff=60,
        )
        self.event = Event.objects.create(
            model=ContentType.objects.get_for_model(Manufacturer),
            object_id=1,
            action=Event.ACTION_CREATE,
            data={"app_label": "assets", "model_name": "manufacturer"},
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)
        super().tearDown()

    def _task_kwargs(self, *, delivery=None, assertion_overrides=None, **delivery_overrides):
        if delivery is None:
            values = {
                "tenant": self.tenant,
                "endpoint": self.endpoint,
                "event": self.event,
                "delivery_id": str(uuid4()),
                "status": WebhookDelivery.STATUS_PENDING,
                "target_url": self.endpoint.url,
                "target_http_method": self.endpoint.http_method,
                "target_headers": dict(self.endpoint.headers),
                "target_secret": self.endpoint.secret,
                "target_enabled": self.endpoint.enabled,
                "target_tenant_id": self.endpoint.tenant_id,
                "target_retry_count": self.endpoint.retry_count,
                "target_retry_backoff": self.endpoint.retry_backoff,
            }
            values.update(delivery_overrides)
            delivery = WebhookDelivery._base_manager.create(**values)
        elif delivery_overrides:
            raise TypeError("delivery overrides require a new durable row")

        assertion_values = {
            "delivery_pk": delivery.pk,
            "delivery_id": UUID(delivery.delivery_id),
            "webhook_endpoint_id": delivery.endpoint_id,
            "event_id": delivery.event_id,
            "tenant_id": delivery.tenant_id,
            "test_send": delivery.test_send,
        }
        assertion_values.update(assertion_overrides or {})
        return {"assertions": WebhookDeliveryAssertions(**assertion_values)}

    @staticmethod
    def _task_delivery(kwargs):
        return WebhookDelivery._base_manager.get(pk=kwargs["assertions"].delivery_pk)

    @staticmethod
    def _response(status_code=200):
        response = MagicMock(status_code=status_code)
        response.raise_for_status.return_value = None
        if status_code >= 500:
            response.raise_for_status.side_effect = requests.HTTPError(response=response)
        return response

    def test_success_persists_record_and_v1_envelope(self):
        response = self._response()
        kwargs = self._task_kwargs()
        with patch("core.http.request_pinned", return_value=response) as request_pinned:
            result = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        payload = json.loads(request_pinned.call_args.kwargs["data"])
        self.assertTrue(result)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(delivery.response_code, 200)
        self.assertEqual(delivery.attempt, 1)
        self.assertIsNotNone(delivery.attempted_at)
        self.assertIsNotNone(delivery.completed_at)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["event_id"], self.event.pk)
        self.assertEqual(payload["delivery_id"], str(kwargs["assertions"].delivery_id))
        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["tenant"], self.tenant.pk)

    def test_retryable_failure_records_jitter_and_schedule_identity(self):
        kwargs = self._task_kwargs()
        response = self._response(503)
        before = timezone.now()
        with (
            patch("core.http.request_pinned", return_value=response),
            patch("extras.tasks.webhooks.random.uniform", return_value=1.0),
            patch("extras.tasks.webhooks.async_task"),
        ):
            result = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        schedule = Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").latest("pk")
        retry_kwargs = ast.literal_eval(schedule.kwargs)
        after = timezone.now()
        self.assertEqual(result.disposition.value, "retryable")
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt, 1)
        self.assertEqual(delivery.error_class, "integration.unavailable")
        self.assertNotIn(self.endpoint.url, delivery.error_message)
        self.assertGreaterEqual(delivery.next_retry_at, before + timedelta(seconds=48))
        self.assertLessEqual(delivery.next_retry_at, after + timedelta(seconds=72))
        self.assertEqual(retry_kwargs["assertions"]["delivery_id"], str(kwargs["assertions"].delivery_id))
        self.assertEqual(retry_kwargs["attempt"], 1)

    def test_retry_budget_exhaustion_marks_dead_after_initial_plus_budget(self):
        # The task reads retry configuration from the endpoint row, so the
        # endpoint must carry the immediate-retry policy this test exercises.
        self.endpoint.retry_backoff = 0
        self.endpoint.save(update_fields=["retry_backoff"])
        kwargs = self._task_kwargs()
        response = self._response(503)
        with (
            patch("core.http.request_pinned", return_value=response),
            patch("extras.tasks.webhooks.async_task"),
        ):
            send_webhook_task(**kwargs, attempt=0)
            send_webhook_task(**kwargs, attempt=1)
            result = send_webhook_task(**kwargs, attempt=2)

        delivery = self._task_delivery(kwargs)
        self.assertEqual(result.disposition.value, "retryable")
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
        self.assertEqual(delivery.attempt, self.endpoint.retry_count + 1)
        self.assertIsNotNone(delivery.completed_at)
        self.assertEqual(Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").count(), 0)

    def test_immediate_retry_enqueue_failure_remains_due_and_recoverable(self):
        from extras.tasks import webhooks as webhook_tasks

        self.endpoint.retry_backoff = 0
        self.endpoint.save(update_fields=["retry_backoff"])
        kwargs = self._task_kwargs()
        with (
            patch("core.http.request_pinned", return_value=self._response(503)),
            patch(
                "extras.tasks.webhooks.async_task",
                side_effect=RuntimeError("broker secret detail"),
            ),
            self.assertLogs("extras.tasks.webhooks", logging.WARNING) as captured,
        ):
            result = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        self.assertEqual(result.disposition, DeliveryDisposition.RETRYABLE)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertIsNotNone(delivery.next_retry_at)
        self.assertLessEqual(delivery.next_retry_at, timezone.now())
        self.assertIsNone(delivery.claim_token)
        self.assertNotIn("broker secret detail", " ".join(captured.output))

        with patch("extras.tasks.webhooks.async_task") as enqueue:
            recovered = webhook_tasks.recover_pending_webhook_deliveries()
        self.assertEqual(recovered, {"dispatched": 1})
        enqueue.assert_called_once()

    def test_http_4xx_and_ssrf_validation_are_terminal_without_schedule(self):
        for side_effect, status_code in ((None, 422), (ValidationError("private secret"), None)):
            with self.subTest(status_code=status_code):
                kwargs = self._task_kwargs()
                if side_effect is None:
                    response = self._response(status_code)
                    request_patch = patch("core.http.request_pinned", return_value=response)
                else:
                    request_patch = patch("core.http.request_pinned", side_effect=side_effect)
                with request_patch, patch("extras.tasks.webhooks.async_task"):
                    result = send_webhook_task(**kwargs)

                delivery = self._task_delivery(kwargs)
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                self.assertEqual(delivery.response_code, status_code)
                self.assertIsNotNone(delivery.completed_at)
                self.assertNotIn("private secret", delivery.error_message)
                self.assertNotIn(self.endpoint.url, delivery.error_message)
        self.assertEqual(Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").count(), 0)

    def test_success_replay_is_a_noop_and_does_not_change_attempt(self):
        kwargs = self._task_kwargs()
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            send_webhook_task(**kwargs)
            replay = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        self.assertEqual(replay.disposition.value, "noop")
        self.assertEqual(request_pinned.call_count, 1)
        self.assertEqual(WebhookDelivery._base_manager.filter(pk=delivery.pk).count(), 1)
        self.assertEqual(delivery.attempt, 1)

    def test_pending_replay_uses_same_record_and_attempt_accounting(self):
        delivery_id = str(uuid4())
        delivery = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=delivery_id,
            status=WebhookDelivery.STATUS_PENDING,
            target_url=self.endpoint.url,
            target_http_method=self.endpoint.http_method,
            target_headers=self.endpoint.headers,
            target_secret=self.endpoint.secret,
            target_enabled=True,
            target_tenant_id=self.endpoint.tenant_id,
            target_retry_count=self.endpoint.retry_count,
            target_retry_backoff=self.endpoint.retry_backoff,
        )
        kwargs = self._task_kwargs(delivery=delivery)
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            send_webhook_task(**kwargs)
            send_webhook_task(**kwargs)

        delivery = WebhookDelivery._base_manager.get(delivery_id=delivery_id)
        self.assertEqual(request_pinned.call_count, 1)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(delivery.attempt, 1)
        self.assertEqual(WebhookDelivery._base_manager.filter(delivery_id=delivery_id).count(), 1)

    def test_event_enqueue_creates_endpoint_and_legacy_pending_records(self):
        endpoint_rule = EventRule.objects.create(
            name="Endpoint rule",
            model=self.event.model,
            events=[self.event.action],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=self.endpoint,
            tenant=self.tenant,
        )
        legacy_event = Event.objects.create(
            model=self.event.model,
            object_id=2,
            action=Event.ACTION_UPDATE,
            data={"app_label": "assets", "model_name": "manufacturer"},
        )
        legacy_rule = EventRule.objects.create(
            name="Legacy rule",
            model=legacy_event.model,
            events=[legacy_event.action],
            action_type=EventRule.ACTION_WEBHOOK,
            action_config={"url": "http://8.8.8.8/legacy-hook", "method": "POST"},
            tenant=self.tenant,
        )
        with patch("extras.services.events.async_task") as async_task:
            process_event_rules(self.event, self.tenant.pk)
            process_event_rules(legacy_event, self.tenant.pk)

        endpoint_delivery = WebhookDelivery._base_manager.get(event=self.event)
        legacy_delivery = WebhookDelivery._base_manager.get(event=legacy_event)
        self.assertEqual(endpoint_delivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(endpoint_delivery.endpoint_id, self.endpoint.pk)
        self.assertEqual(endpoint_delivery.tenant_id, self.tenant.pk)
        self.assertEqual(endpoint_delivery.event_rule_id, endpoint_rule.pk)
        self.assertEqual(endpoint_delivery.target_url, self.endpoint.url)
        self.assertIsNone(legacy_delivery.endpoint_id)
        self.assertEqual(legacy_delivery.tenant_id, self.tenant.pk)
        self.assertEqual(legacy_delivery.event_rule_id, legacy_rule.pk)
        self.assertEqual(legacy_delivery.target_url, "http://8.8.8.8/legacy-hook")
        self.assertEqual(async_task.call_count, 2)
        endpoint_rule.delete()
        legacy_rule.delete()

    def test_endpointless_deliveries_keep_their_exact_rule_targets(self):
        original_targets = (
            ("http://8.8.8.8/first-rule", "first-rule-secret"),
            ("http://1.1.1.1/second-rule", "second-rule-secret"),
        )
        rules = [
            EventRule.objects.create(
                name=f"Legacy rule {index}",
                model=self.event.model,
                events=[self.event.action],
                action_type=EventRule.ACTION_WEBHOOK,
                action_config={"url": url, "method": "POST", "secret": secret},
                tenant=self.tenant,
            )
            for index, (url, secret) in enumerate(original_targets, start=1)
        ]
        with patch("extras.services.events.async_task") as async_task:
            process_event_rules(self.event, self.tenant.pk)

        self.assertEqual(async_task.call_count, 2)
        for queued in async_task.call_args_list:
            rendered = repr(queued)
            for url, secret in original_targets:
                self.assertNotIn(url, rendered)
                self.assertNotIn(secret, rendered)

        rules[0].action_config = {"url": "http://9.9.9.9/mutated", "secret": "mutated-secret"}
        rules[0].save(update_fields=["action_config"])
        rules[1].delete()

        response = self._response()
        with (
            patch("extras.tasks.webhooks._dispatch_webhook_request", return_value=response) as transport,
            self.assertLogs("extras.tasks.webhooks", level="INFO") as captured,
        ):
            for queued in async_task.call_args_list:
                send_webhook_task(queued.args[1], **queued.kwargs)

        self.assertEqual(
            [(call.kwargs["url"], call.kwargs["secret"]) for call in transport.call_args_list],
            list(original_targets),
        )
        rendered_logs = "\n".join(captured.output)
        for url, secret in original_targets:
            self.assertNotIn(url, rendered_logs)
            self.assertNotIn(secret, rendered_logs)

    def test_live_duplicate_worker_does_not_reach_transport(self):
        kwargs = self._task_kwargs()
        assertions = kwargs["assertions"]
        first_request_started = threading.Event()
        release_first_request = threading.Event()
        transport_lock = threading.Lock()
        transport_calls = 0

        def transport(*_args, **_kwargs):
            nonlocal transport_calls
            with transport_lock:
                transport_calls += 1
                call_number = transport_calls
            if call_number == 1:
                first_request_started.set()
                self.assertTrue(release_first_request.wait(timeout=10))
            return self._response()

        def worker():
            close_old_connections()
            try:
                return send_webhook_task(assertions)
            finally:
                close_old_connections()

        with patch("extras.tasks.webhooks._dispatch_webhook_request", side_effect=transport):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(worker)
                self.assertTrue(first_request_started.wait(timeout=10))
                duplicate = executor.submit(worker)
                duplicate_result = duplicate.result(timeout=10)
                release_first_request.set()
                first_result = first.result(timeout=10)

        self.assertTrue(first_result)
        self.assertEqual(duplicate_result.disposition.value, "noop")
        self.assertEqual(transport_calls, 1)

    def test_expired_claim_is_recoverable(self):
        kwargs = self._task_kwargs(
            claim_token=uuid4(),
            claim_expires_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            result = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        self.assertTrue(result)
        self.assertEqual(request_pinned.call_count, 1)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertIsNone(delivery.claim_token)
        self.assertIsNone(delivery.claim_expires_at)

    def test_recovery_sweep_requeues_stranded_pending_once(self):
        from extras.tasks import webhooks as webhook_tasks

        now = timezone.now()
        kwargs = self._task_kwargs()
        delivery = self._task_delivery(kwargs)
        WebhookDelivery._base_manager.filter(pk=delivery.pk).update(created_at=now - timedelta(minutes=5))

        with (
            patch("extras.tasks.webhooks.timezone.now", return_value=now),
            patch("extras.tasks.webhooks.async_task") as async_task,
        ):
            result = webhook_tasks.recover_pending_webhook_deliveries()
            repeated = webhook_tasks.recover_pending_webhook_deliveries()

        self.assertEqual(result, {"dispatched": 1})
        self.assertEqual(repeated, {"dispatched": 0})
        async_task.assert_called_once()
        delivery.refresh_from_db()
        self.assertGreater(delivery.dispatch_stale_at, now)
        self.assertEqual(async_task.call_args.args[0], "extras.tasks.webhooks.send_webhook_task")
        self.assertEqual(async_task.call_args.args[1].delivery_pk, delivery.pk)

    def test_recovery_sweep_skips_live_claim_and_requeues_expired_claim(self):
        from extras.tasks import webhooks as webhook_tasks

        now = timezone.now()
        live_kwargs = self._task_kwargs(
            claim_token=uuid4(),
            claim_expires_at=now + timedelta(minutes=5),
        )
        expired_kwargs = self._task_kwargs(
            claim_token=uuid4(),
            claim_expires_at=now - timedelta(seconds=1),
            attempted_at=now - timedelta(minutes=2),
            attempt=2,
        )
        live = self._task_delivery(live_kwargs)
        expired = self._task_delivery(expired_kwargs)
        WebhookDelivery._base_manager.filter(pk__in=(live.pk, expired.pk)).update(created_at=now - timedelta(minutes=5))

        with (
            patch("extras.tasks.webhooks.timezone.now", return_value=now),
            patch("extras.tasks.webhooks.async_task") as async_task,
        ):
            result = webhook_tasks.recover_pending_webhook_deliveries()

        self.assertEqual(result, {"dispatched": 1})
        async_task.assert_called_once()
        self.assertEqual(async_task.call_args.args[1].delivery_pk, expired.pk)
        self.assertEqual(async_task.call_args.kwargs["attempt"], 2)
        live.refresh_from_db()
        self.assertIsNone(live.dispatch_stale_at)

    def test_recovery_enqueue_failure_releases_lease_and_redacts_error(self):
        from extras.tasks import webhooks as webhook_tasks

        now = timezone.now()
        kwargs = self._task_kwargs(
            status=WebhookDelivery.STATUS_FAILED,
            attempted_at=now - timedelta(minutes=2),
            next_retry_at=now - timedelta(seconds=1),
        )
        delivery = self._task_delivery(kwargs)
        canary = "issue445-recovery-broker-secret"
        with (
            patch("extras.tasks.webhooks.timezone.now", return_value=now),
            patch("extras.tasks.webhooks.async_task", side_effect=RuntimeError(canary)),
            self.assertLogs("extras.tasks.webhooks", level="ERROR") as captured,
        ):
            result = webhook_tasks.recover_pending_webhook_deliveries()

        self.assertEqual(result, {"dispatched": 1})
        delivery.refresh_from_db()
        self.assertEqual(delivery.dispatch_stale_at, now)
        rendered = " ".join(captured.output)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn(canary, rendered)

    def test_unexpected_runtime_failure_releases_claim_and_schedules_retry(self):
        kwargs = self._task_kwargs()
        canary = "issue445-unexpected-secret-canary"
        with (
            patch("extras.tasks.webhooks._dispatch_webhook_request", side_effect=OSError(canary)),
            self.assertLogs("extras.tasks.webhooks", level="ERROR") as captured,
        ):
            result = send_webhook_task(**kwargs)

        delivery = self._task_delivery(kwargs)
        self.assertEqual(result.disposition, DeliveryDisposition.RETRYABLE)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertIsNone(delivery.claim_token)
        self.assertIsNone(delivery.claim_expires_at)
        self.assertTrue(Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").exists())
        self.assertNotIn(canary, " ".join(captured.output))
        self.assertIn("OSError", " ".join(captured.output))

    def test_expired_pending_claim_can_be_manually_redelivered(self):
        kwargs = self._task_kwargs(
            claim_token=uuid4(),
            claim_expires_at=timezone.now() - timedelta(seconds=1),
        )
        source = self._task_delivery(kwargs)
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(source.pk, actor_id=self.actor.pk)

        self.assertEqual(redelivery.redelivered_from_id, source.pk)
        self.assertEqual(redelivery.target_url, source.target_url)
        async_task.assert_called_once()

    def test_malformed_target_secrets_and_headers_fail_closed(self):
        self.assertIsNone(_decrypt_target_secret("plaintext-secret"))
        self.assertIsNone(_decrypt_target_secret("enc$malformed-ciphertext"))
        with self.assertRaises(ValidationError):
            _encrypted_secret_snapshot(123)

        self.endpoint.secret = "plaintext-snapshot-secret"
        snapshot = _endpoint_target_snapshot(self.endpoint)
        self.assertTrue(snapshot["target_secret"].startswith("enc$"))
        self.assertNotIn("plaintext-snapshot-secret", snapshot["target_secret"])
        self.endpoint.headers = ["not", "a", "mapping"]
        with self.assertRaises(ValidationError):
            _endpoint_target_snapshot(self.endpoint)

    def test_stale_worker_cannot_finish_a_newer_claim(self):
        live_token = uuid4()
        kwargs = self._task_kwargs(
            claim_token=live_token,
            claim_expires_at=timezone.now() + timedelta(minutes=5),
        )
        delivery = self._task_delivery(kwargs)
        result = _finish_delivery(
            delivery_pk=delivery.pk,
            claim_token=uuid4(),
            result=DeliveryResult("webhook.deliver", DeliveryDisposition.SUCCESS),
            response_code=200,
            retry_count=0,
            retry_backoff=0,
            retry_kwargs=None,
        )

        delivery.refresh_from_db()
        self.assertEqual(result.disposition, DeliveryDisposition.NOOP)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(delivery.claim_token, live_token)

    def test_redelivery_creates_new_record_and_keeps_source_unchanged(self):
        source = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_SUCCESS,
            attempt=2,
            response_code=200,
            completed_at=timezone.now(),
            target_url=self.endpoint.url,
            target_http_method=self.endpoint.http_method,
            target_headers=self.endpoint.headers,
            target_secret=self.endpoint.secret,
            target_enabled=True,
            target_tenant_id=self.endpoint.tenant_id,
            target_retry_count=self.endpoint.retry_count,
            target_retry_backoff=self.endpoint.retry_backoff,
        )
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(source.pk, actor_id=self.actor.pk)

        source.refresh_from_db()
        self.assertNotEqual(redelivery.delivery_id, source.delivery_id)
        self.assertEqual(redelivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(redelivery.redelivered_from_id, source.pk)
        self.assertEqual(redelivery.redelivered_by_id, self.actor.pk)
        self.assertIsNotNone(redelivery.redelivered_at)
        self.assertEqual(source.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(source.attempt, 2)
        task_args, task_kwargs = async_task.call_args
        assertions = task_args[1]
        self.assertEqual(str(assertions.delivery_id), redelivery.delivery_id)
        self.assertEqual(assertions.delivery_pk, redelivery.pk)
        self.assertNotIn(self.endpoint.url, repr((task_args, task_kwargs)))
        self.assertNotIn(self.endpoint.secret_decrypted, repr((task_args, task_kwargs)))

    def test_redelivery_refuses_pending_and_future_retry(self):
        pending = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_PENDING,
        )
        future = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_FAILED,
            next_retry_at=timezone.now() + timedelta(minutes=5),
        )
        for delivery in (pending, future):
            with self.assertRaisesRegex(ValidationError, "Delivery is still in progress"):
                redeliver_webhook_delivery(delivery.pk, actor_id=self.actor.pk)

    def test_redelivery_tenant_mismatch_fails_closed(self):
        operator = User.objects.create_user(username="delivery-operator", password="password")
        role = Role.objects.create(
            tenant=self.other_tenant,
            name="Other tenant operator",
            permissions=["extras.view_webhookendpoint", "extras.change_webhookendpoint"],
        )
        membership = grant(operator, self.other_tenant, role).membership
        source = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
        )
        set_current_tenant(self.other_tenant)
        set_current_membership(membership)
        with self.assertRaisesRegex(PermissionDenied, "Delivery not found"):
            redeliver_webhook_delivery(source.pk, actor_id=operator.pk)

    def test_test_send_creates_record_and_uses_test_payload(self):
        with patch("extras.tasks.webhooks.async_task") as async_task:
            delivery = send_webhook_test(self.endpoint.pk, actor_id=self.actor.pk)
            task_args, task_kwargs = async_task.call_args

        self.assertTrue(delivery.test_send)
        self.assertIsNone(delivery.event_id)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_PENDING)
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            send_webhook_task(task_args[1], **task_kwargs)
        payload = json.loads(request_pinned.call_args.kwargs["data"])
        delivery.refresh_from_db()
        self.assertEqual(payload["event_id"], None)
        self.assertEqual(payload["event"], "test")
        self.assertEqual(payload["model"], "extras.WebhookEndpoint")
        self.assertEqual(payload["object_id"], self.endpoint.pk)
        self.assertEqual(payload["data"], {})
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)

    def test_test_send_retry_preserves_payload_timestamp(self):
        fixed_timestamp = timezone.now() - timedelta(days=1)
        kwargs = self._task_kwargs(
            event=None,
            test_send=True,
            payload_timestamp=fixed_timestamp,
        )
        responses = (self._response(503), self._response(200))
        with patch("core.http.request_pinned", side_effect=responses) as request_pinned:
            first = send_webhook_task(**kwargs)
            schedule = Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").latest("pk")
            retry_kwargs = ast.literal_eval(schedule.kwargs)
            second = send_webhook_task(**retry_kwargs)

        self.assertEqual(first.disposition, DeliveryDisposition.RETRYABLE)
        self.assertEqual(second.disposition, DeliveryDisposition.SUCCESS)
        payloads = [json.loads(call.kwargs["data"]) for call in request_pinned.call_args_list]
        self.assertEqual([payload["timestamp"] for payload in payloads], [fixed_timestamp.isoformat()] * 2)
        self.assertEqual([payload["attempt"] for payload in payloads], [1, 2])

    def test_failure_records_never_persist_url_secret_headers_or_response_text(self):
        secret = self.endpoint.secret_decrypted
        response_body = "remote response body with secret marker"
        http_response = self._response(503)
        cases = (
            requests.ConnectionError(response_body),
            requests.HTTPError(response=http_response),
            ValidationError(f"blocked {secret} {response_body}"),
        )
        for failure in cases:
            with self.subTest(type=type(failure).__name__):
                kwargs = self._task_kwargs(target_headers={"Authorization": secret})
                if isinstance(failure, requests.HTTPError):
                    http_response._content = response_body.encode()
                with patch("core.http.request_pinned", side_effect=failure), patch("extras.tasks.webhooks.async_task"):
                    send_webhook_task(**kwargs)
                delivery = self._task_delivery(kwargs)
                self.assertNotIn(self.endpoint.url, delivery.error_message)
                self.assertNotIn(secret, delivery.error_message)
                self.assertNotIn("Authorization", delivery.error_message)
                self.assertNotIn(response_body, delivery.error_message)

    def test_mismatched_replay_fails_closed(self):
        other_endpoint = WebhookEndpoint._base_manager.create(
            name="Other hook",
            url="http://8.8.8.8/other",
            tenant=self.tenant,
        )
        cases = (
            {"webhook_endpoint_id": other_endpoint.pk},
            {"event_id": 999999},
            {"tenant_id": self.other_tenant.pk},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                kwargs = self._task_kwargs(
                    status=WebhookDelivery.STATUS_FAILED,
                    assertion_overrides=overrides,
                )
                delivery = self._task_delivery(kwargs)
                before = {field.attname: getattr(delivery, field.attname) for field in delivery._meta.concrete_fields}
                with patch("core.http.request_pinned") as request_pinned:
                    result = send_webhook_task(**kwargs)
                delivery.refresh_from_db()
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(
                    {field.attname: getattr(delivery, field.attname) for field in delivery._meta.concrete_fields},
                    before,
                )
                request_pinned.assert_not_called()

    def test_invalid_targets_fail_closed(self):
        cases = (
            {"target_url": ""},
            {"target_enabled": False},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                kwargs = self._task_kwargs(**overrides)
                with patch("core.http.request_pinned") as request_pinned:
                    result = send_webhook_task(**kwargs)
                delivery = self._task_delivery(kwargs)
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                request_pinned.assert_not_called()

        immutable_kwargs = self._task_kwargs()
        self.endpoint.enabled = False
        self.endpoint.url = "http://9.9.9.9/mutated-after-enqueue"
        self.endpoint.save(update_fields=["enabled", "url"])
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            result = send_webhook_task(**immutable_kwargs)
        self.assertTrue(result)
        self.assertEqual(request_pinned.call_args.args[1], "http://8.8.8.8/durable-hook")
        self.endpoint.enabled = True
        self.endpoint.url = "http://8.8.8.8/durable-hook"
        self.endpoint.save(update_fields=["enabled", "url"])

        cross_tenant_kwargs = self._task_kwargs(
            tenant=self.other_tenant,
        )
        with patch("core.http.request_pinned") as request_pinned:
            result = send_webhook_task(**cross_tenant_kwargs)
        self.assertEqual(result.disposition.value, "terminal")
        request_pinned.assert_not_called()

    def test_unknown_event_and_tenant_references_fail_closed(self):
        kwargs = self._task_kwargs(event=None)
        with patch("core.http.request_pinned") as request_pinned:
            result = send_webhook_task(**kwargs)
        delivery = self._task_delivery(kwargs)
        self.assertEqual(result.disposition.value, "terminal")
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
        request_pinned.assert_not_called()

    def test_broken_actor_permission_guard_fails_closed(self):
        from types import SimpleNamespace

        from extras.tasks.webhooks import _is_platform_actor

        broken = SimpleNamespace(is_authenticated=True, is_superuser=False)
        self.assertFalse(_is_platform_actor(broken))
        self.assertFalse(_is_platform_actor(None))

    def test_finish_is_noop_when_record_turns_terminal_mid_flight(self):
        kwargs = self._task_kwargs()
        delivery = self._task_delivery(kwargs)

        def _mutate(*_args, **_ignored):
            WebhookDelivery._base_manager.filter(pk=delivery.pk).update(status="dead")
            return self._response()

        with patch("core.http.request_pinned", side_effect=_mutate):
            result = send_webhook_task(**kwargs)
        self.assertEqual(result.disposition.value, "noop")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)

    def test_slack_and_teams_test_send_carries_test_fields(self):
        for host in ("https://hooks.slack.com/services/test", "https://tenant.webhook.office.com/webhookb2/test"):
            with self.subTest(host=host):
                endpoint = WebhookEndpoint._base_manager.create(
                    name=f"Chat hook {host[:20]}",
                    url=host,
                    tenant=self.tenant,
                )
                with patch("extras.tasks.webhooks.async_task") as async_task:
                    delivery = send_webhook_test(endpoint.pk, actor_id=self.actor.pk)
                    task_args, task_kwargs = async_task.call_args
                with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
                    send_webhook_task(task_args[1], **task_kwargs)
                payload = request_pinned.call_args.kwargs["json"]
                self.assertEqual(payload["event"], "test")
                self.assertEqual(payload["object_id"], endpoint.pk)
                self.assertEqual(payload["schema_version"], 1)
                delivery.refresh_from_db()
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)

    def test_redelivery_variants(self):
        test_delivery = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
            test_send=True,
            payload_timestamp=timezone.now(),
            target_url=self.endpoint.url,
            target_http_method=self.endpoint.http_method,
            target_headers=self.endpoint.headers,
            target_secret=self.endpoint.secret,
            target_enabled=True,
            target_tenant_id=self.endpoint.tenant_id,
            target_retry_count=self.endpoint.retry_count,
            target_retry_backoff=self.endpoint.retry_backoff,
        )
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(test_delivery.pk, actor_id=self.actor.pk)
        task_args, task_kwargs = async_task.call_args
        assertions = task_args[1]
        self.assertTrue(redelivery.test_send)
        self.assertTrue(assertions.test_send)
        self.assertIsNone(assertions.event_id)
        self.assertNotIn(self.endpoint.url, repr((task_args, task_kwargs)))

        legacy_rule = EventRule.objects.create(
            name="Legacy redeliver rule",
            model=self.event.model,
            events=[self.event.action],
            action_type=EventRule.ACTION_WEBHOOK,
            action_config={"url": "http://8.8.8.8/legacy", "method": "POST"},
            tenant=self.tenant,
        )
        legacy_delivery = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
            event_rule_id=legacy_rule.pk,
            target_url="http://8.8.8.8/legacy",
            target_http_method="POST",
            target_headers={},
            target_secret="",
            target_enabled=True,
            target_tenant_id=None,
            target_retry_count=3,
            target_retry_backoff=60,
        )
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(legacy_delivery.pk, actor_id=self.actor.pk)
        task_args, task_kwargs = async_task.call_args
        assertions = task_args[1]
        self.assertEqual(redelivery.target_url, "http://8.8.8.8/legacy")
        self.assertEqual(redelivery.event_rule_id, legacy_rule.pk)
        self.assertIsNone(assertions.webhook_endpoint_id)
        self.assertNotIn("http://8.8.8.8/legacy", repr((task_args, task_kwargs)))

        orphan = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
        )
        with self.assertRaises(ValidationError):
            redeliver_webhook_delivery(orphan.pk, actor_id=self.actor.pk)

        with self.assertRaises(PermissionDenied):
            redeliver_webhook_delivery(legacy_delivery.pk, actor_id=None)
        legacy_rule.delete()

    def test_platform_permission_user_can_redeliver_system_wide(self):
        platform_user = User.objects.create_user(username="delivery-platform-viewer", password="password")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Platform delivery viewer",
            permissions=["extras.view_webhookdelivery", "extras.change_webhookendpoint"],
        )
        grant(platform_user, self.tenant, role)

        global_delivery = WebhookDelivery._base_manager.create(
            tenant=None,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
            target_url=self.endpoint.url,
            target_http_method=self.endpoint.http_method,
            target_headers=self.endpoint.headers,
            target_secret=self.endpoint.secret,
            target_enabled=True,
            target_tenant_id=None,
            target_retry_count=self.endpoint.retry_count,
            target_retry_backoff=self.endpoint.retry_backoff,
        )
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(global_delivery.pk, actor_id=platform_user.pk)
        self.assertEqual(redelivery.redelivered_by_id, platform_user.pk)
        self.assertEqual(redelivery.tenant_id, None)
        async_task.assert_called_once()

        operator = User.objects.create_user(username="delivery-plain-operator", password="password")
        with self.assertRaises(PermissionDenied):
            redeliver_webhook_delivery(global_delivery.pk, actor_id=operator.pk)

    def test_test_send_gating_and_payloads(self):
        global_endpoint = WebhookEndpoint._base_manager.create(
            name="Global hook",
            url="http://8.8.8.8/global",
            tenant=None,
        )
        platform_user = User.objects.create_user(username="delivery-global-operator", password="password")
        grant(
            platform_user,
            self.tenant,
            Role.objects.create(
                tenant=self.tenant,
                name="Global operator",
                permissions=["extras.view_webhookdelivery", "extras.change_webhookendpoint"],
            ),
        )
        with patch("extras.tasks.webhooks.async_task"):
            delivery = send_webhook_test(global_endpoint.pk, actor_id=platform_user.pk)
        self.assertTrue(delivery.test_send)
        self.assertIsNone(delivery.tenant_id)

        operator = User.objects.create_user(username="delivery-tenant-operator-2", password="password")
        grant(
            operator,
            self.tenant,
            Role.objects.create(
                tenant=self.tenant,
                name="Tenant operator 2",
                permissions=["extras.view_webhookendpoint", "extras.change_webhookendpoint"],
            ),
        )
        with self.assertRaises(PermissionDenied):
            send_webhook_test(global_endpoint.pk, actor_id=operator.pk)
        with self.assertRaises(PermissionDenied):
            send_webhook_test(999999, actor_id=operator.pk)
        with self.assertRaises(PermissionDenied):
            send_webhook_test(global_endpoint.pk, actor_id=None)

    def test_delivery_str_contains_identity(self):
        delivery = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_PENDING,
        )
        self.assertIn(delivery.delivery_id, str(delivery))
        self.assertIn(delivery.status, str(delivery))


def _prepare_historical_extras_migration_state():
    recorder = MigrationRecorder(connection)
    if recorder.migration_qs.filter(
        app="extras",
        name="0113_upgrade_legacy_webhook_retry_schedules",
    ).exists():
        recorder.record_unapplied("extras", "0113_upgrade_legacy_webhook_retry_schedules")


@pytest.mark.serial_only
class WebhookDeliveryMigrationTests(TransactionTestCase):
    """Migration 0109 creates only the durable table and reverses cleanly."""

    migrate_from = ("extras", "0108_alertlog_delivery_outcome")
    migrate_to = ("extras", "0109_webhookdelivery")

    def setUp(self):
        super().setUp()
        _prepare_historical_extras_migration_state()
        self.executor = MigrationExecutor(connection)

    def _migrate(self, target):
        self.executor = MigrationExecutor(connection)
        return self.executor.migrate([target])

    def tearDown(self):
        try:
            MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_reverse_reapply_has_no_backfill_operation(self):
        migration_module = importlib.import_module("extras.migrations.0109_webhookdelivery")
        self.assertFalse(any(isinstance(operation, RunPython) for operation in migration_module.Migration.operations))

        old_apps = self._migrate(self.migrate_from).apps
        old_extras_models = {
            model._meta.model_name for model in old_apps.get_models() if model._meta.app_label == "extras"
        }
        self.assertNotIn("webhookdelivery", old_extras_models)
        new_apps = self._migrate(self.migrate_to).apps
        Delivery = new_apps.get_model("extras", "WebhookDelivery")
        row = Delivery.objects.create(delivery_id=str(uuid4()), status="pending")
        table_name = Delivery._meta.db_table
        self.assertIsNotNone(row.pk)

        self._migrate(self.migrate_from)
        self.assertNotIn(table_name, connection.introspection.table_names())
        self._migrate(self.migrate_to)
        self.assertIn(table_name, connection.introspection.table_names())


@pytest.mark.serial_only
class WebhookDeliveryTargetMigrationTests(TransactionTestCase):
    """Endpoint-backed history gains snapshots; ambiguous legacy rows stay inert."""

    migrate_from = ("extras", "0110_issue445_task_paths")
    migrate_to = ("extras", "0112_backfill_webhookdelivery_targets")

    def setUp(self):
        super().setUp()
        _prepare_historical_extras_migration_state()
        self.executor = MigrationExecutor(connection)

    def _migrate(self, target):
        self.executor = MigrationExecutor(connection)
        return self.executor.migrate([target])

    def tearDown(self):
        try:
            MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_reverse_reapply_backfills_only_endpoint_targets(self):
        old_apps = self._migrate(self.migrate_from).apps
        Endpoint = old_apps.get_model("extras", "WebhookEndpoint")
        Delivery = old_apps.get_model("extras", "WebhookDelivery")
        endpoint = Endpoint.objects.create(
            name="Migration endpoint",
            url="https://example.invalid/migration-target",
            http_method="PATCH",
            headers={"X-Migration": "header"},
            secret="enc$migration-ciphertext",
            retry_count=7,
            retry_backoff=23,
        )
        linked = Delivery.objects.create(endpoint_id=endpoint.pk, delivery_id=str(uuid4()), status="pending")
        plaintext_endpoint = Endpoint.objects.create(
            name="Legacy plaintext migration endpoint",
            url="https://example.invalid/plaintext-target",
            secret="legacy-plaintext-secret",
        )
        plaintext = Delivery.objects.create(
            endpoint_id=plaintext_endpoint.pk,
            delivery_id=str(uuid4()),
            status="pending",
        )
        endpointless = Delivery.objects.create(delivery_id=str(uuid4()), status="pending")

        new_apps = self._migrate(self.migrate_to).apps
        MigratedDelivery = new_apps.get_model("extras", "WebhookDelivery")
        linked = MigratedDelivery.objects.get(pk=linked.pk)
        plaintext = MigratedDelivery.objects.get(pk=plaintext.pk)
        endpointless = MigratedDelivery.objects.get(pk=endpointless.pk)
        self.assertEqual(linked.target_url, endpoint.url)
        self.assertEqual(linked.target_http_method, "PATCH")
        self.assertEqual(linked.target_headers, {"X-Migration": "header"})
        self.assertEqual(linked.target_secret, "enc$migration-ciphertext")
        self.assertEqual(linked.target_retry_count, 7)
        self.assertEqual(linked.target_retry_backoff, 23)
        self.assertTrue(plaintext.target_secret.startswith("enc$"))
        self.assertEqual(_decrypt_target_secret(plaintext.target_secret), "legacy-plaintext-secret")
        self.assertEqual(endpointless.target_url, "")
        self.assertEqual(endpointless.target_secret, "")

        self._migrate(self.migrate_from)
        reapplied_apps = self._migrate(self.migrate_to).apps
        ReappliedDelivery = reapplied_apps.get_model("extras", "WebhookDelivery")
        self.assertEqual(ReappliedDelivery.objects.get(pk=linked.pk).target_url, endpoint.url)
        reapplied_plaintext = ReappliedDelivery.objects.get(pk=plaintext.pk)
        self.assertEqual(_decrypt_target_secret(reapplied_plaintext.target_secret), "legacy-plaintext-secret")
        self.assertEqual(ReappliedDelivery.objects.get(pk=endpointless.pk).target_url, "")


@pytest.mark.serial_only
class WebhookRetryScheduleMigrationTests(TransactionTestCase):
    """Legacy delayed retries become assertion-only schedules with no target secrets."""

    migrate_from = ("extras", "0112_backfill_webhookdelivery_targets")
    migrate_to = ("extras", "0113_upgrade_legacy_webhook_retry_schedules")

    def setUp(self):
        super().setUp()
        _prepare_historical_extras_migration_state()

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        return executor.migrate([target])

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_executes_legacy_retry_and_reverse_is_refused(self):
        old_apps = self._migrate(self.migrate_from).apps
        Delivery = old_apps.get_model("extras", "WebhookDelivery")
        HistoricalSchedule = old_apps.get_model("django_q", "Schedule")
        delivery = Delivery.objects.create(
            delivery_id=str(uuid4()),
            status="failed",
            test_send=True,
        )
        legacy = {
            "url": "https://example.invalid/legacy-retry",
            "method": "POST",
            "headers": {"Authorization": "Bearer legacy-header-secret"},
            "secret": "legacy-hmac-secret",
            "webhook_endpoint_id": None,
            "event_id": None,
            "delivery_id": delivery.delivery_id,
            "tenant_id": None,
            "event_action": "test",
            "event_model_app_label": "extras",
            "event_model_name": "webhookendpoint",
            "event_object_id": 1,
            "event_timestamp_iso": "2026-01-01T00:00:00+00:00",
            "event_data": {},
            "attempt": 1,
            "retry_count": 3,
            "retry_backoff": 60,
            "actor_id": None,
            "request_id": "issue445-migration",
            "test_send": True,
        }
        schedule = HistoricalSchedule.objects.create(
            name="Legacy webhook retry",
            func="extras.tasks.webhooks.send_webhook_task",
            kwargs=repr(legacy),
            schedule_type="O",
            repeats=1,
        )
        non_kwargs = {
            field: getattr(schedule, field)
            for field in ("pk", "name", "func", "schedule_type", "repeats", "hook", "args")
        }

        new_apps = self._migrate(self.migrate_to).apps
        NewDelivery = new_apps.get_model("extras", "WebhookDelivery")
        NewSchedule = new_apps.get_model("django_q", "Schedule")
        upgraded_delivery = NewDelivery.objects.get(pk=delivery.pk)
        upgraded_schedule = NewSchedule.objects.get(pk=schedule.pk)
        parsed = ast.literal_eval(upgraded_schedule.kwargs)
        self.assertEqual(set(parsed), {"assertions", "attempt", "actor_id", "request_id"})
        self.assertEqual(parsed["assertions"]["delivery_pk"], delivery.pk)
        self.assertEqual(parsed["assertions"]["delivery_id"], delivery.delivery_id)
        self.assertNotIn("url", upgraded_schedule.kwargs)
        self.assertNotIn("legacy-header-secret", upgraded_schedule.kwargs)
        self.assertNotIn("legacy-hmac-secret", upgraded_schedule.kwargs)
        self.assertEqual(upgraded_delivery.target_url, legacy["url"])
        self.assertEqual(upgraded_delivery.payload_timestamp.isoformat(), legacy["event_timestamp_iso"])
        self.assertEqual(_decrypt_target_secret(upgraded_delivery.target_secret), "legacy-hmac-secret")
        for field, value in non_kwargs.items():
            self.assertEqual(getattr(upgraded_schedule, field), value)

        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        with patch("core.http.request_pinned", return_value=response) as request_pinned:
            result = send_webhook_task(
                parsed["assertions"],
                attempt=parsed["attempt"],
                actor_id=parsed["actor_id"],
                request_id=parsed["request_id"],
            )
        self.assertEqual(result.disposition, DeliveryDisposition.SUCCESS)
        request_pinned.assert_called_once()
        upgraded_delivery.refresh_from_db()
        self.assertEqual(upgraded_delivery.status, WebhookDelivery.STATUS_SUCCESS)

        with self.assertRaisesRegex(RuntimeError, r"^issue445\.webhook_retry_upgrade\.reverse_refused$"):
            self._migrate(self.migrate_from)

    def test_malformed_legacy_retry_payload_fails_closed(self):
        old_apps = self._migrate(self.migrate_from).apps
        HistoricalSchedule = old_apps.get_model("django_q", "Schedule")
        HistoricalSchedule.objects.create(
            name="Malformed legacy webhook retry",
            func="extras.tasks.webhooks.send_webhook_task",
            kwargs=repr({"url": "https://example.invalid/leaks-secret", "secret": "must-not-appear"}),
            schedule_type="O",
            repeats=1,
        )
        with self.assertRaisesRegex(RuntimeError, r"^issue445\.webhook_retry_upgrade\."):
            self._migrate(self.migrate_to)
        HistoricalSchedule.objects.filter(name="Malformed legacy webhook retry").delete()
