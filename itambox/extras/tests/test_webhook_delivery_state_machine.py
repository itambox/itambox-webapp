import ast
import importlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations.special import RunPython
from django.test import TransactionTestCase
from django.utils import timezone
from django_q.models import Schedule

from assets.models import Manufacturer
from core.events import process_event_rules
from core.managers import set_current_membership, set_current_tenant
from core.tasks.webhooks import (
    redeliver_webhook_delivery,
    send_webhook_task,
    send_webhook_test,
)
from core.tests.mixins import TenantTestMixin, grant
from extras.models import Event, EventRule, WebhookDelivery, WebhookEndpoint
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

    def _task_kwargs(self, **overrides):
        values = {
            "url": self.endpoint.url,
            "method": "POST",
            "headers": {},
            "secret": "",
            "webhook_endpoint_id": self.endpoint.pk,
            "event_id": self.event.pk,
            "delivery_id": str(uuid4()),
            "tenant_id": self.tenant.pk,
            "event_action": "create",
            "event_model_app_label": "assets",
            "event_model_name": "manufacturer",
            "event_object_id": 1,
            "event_timestamp_iso": self.event.timestamp.isoformat(),
            "event_data": self.event.data,
            "retry_count": self.endpoint.retry_count,
            "retry_backoff": self.endpoint.retry_backoff,
        }
        values.update(overrides)
        return values

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

        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        payload = json.loads(request_pinned.call_args.kwargs["data"])
        self.assertTrue(result)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(delivery.response_code, 200)
        self.assertEqual(delivery.attempt, 1)
        self.assertIsNotNone(delivery.attempted_at)
        self.assertIsNotNone(delivery.completed_at)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["event_id"], self.event.pk)
        self.assertEqual(payload["delivery_id"], kwargs["delivery_id"])
        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["tenant"], self.tenant.pk)

    def test_retryable_failure_records_jitter_and_schedule_identity(self):
        kwargs = self._task_kwargs()
        response = self._response(503)
        before = timezone.now()
        with (
            patch("core.http.request_pinned", return_value=response),
            patch("core.tasks.webhooks.random.uniform", return_value=1.0),
            patch("core.tasks.webhooks.async_task"),
        ):
            result = send_webhook_task(**kwargs)

        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        schedule = Schedule.objects.filter(func="core.tasks.send_webhook_task").latest("pk")
        retry_kwargs = ast.literal_eval(schedule.kwargs)
        after = timezone.now()
        self.assertEqual(result.disposition.value, "retryable")
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt, 1)
        self.assertEqual(delivery.error_class, "integration.unavailable")
        self.assertNotIn(self.endpoint.url, delivery.error_message)
        self.assertGreaterEqual(delivery.next_retry_at, before + timedelta(seconds=48))
        self.assertLessEqual(delivery.next_retry_at, after + timedelta(seconds=72))
        self.assertEqual(retry_kwargs["delivery_id"], kwargs["delivery_id"])
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
            patch("core.tasks.webhooks.async_task"),
        ):
            send_webhook_task(**kwargs, attempt=0)
            send_webhook_task(**kwargs, attempt=1)
            result = send_webhook_task(**kwargs, attempt=2)

        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        self.assertEqual(result.disposition.value, "retryable")
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
        self.assertEqual(delivery.attempt, self.endpoint.retry_count + 1)
        self.assertIsNotNone(delivery.completed_at)
        self.assertEqual(Schedule.objects.filter(func="core.tasks.send_webhook_task").count(), 0)

    def test_http_4xx_and_ssrf_validation_are_terminal_without_schedule(self):
        for side_effect, status_code in ((None, 422), (ValidationError("private secret"), None)):
            with self.subTest(status_code=status_code):
                kwargs = self._task_kwargs()
                if side_effect is None:
                    response = self._response(status_code)
                    request_patch = patch("core.http.request_pinned", return_value=response)
                else:
                    request_patch = patch("core.http.request_pinned", side_effect=side_effect)
                with request_patch, patch("core.tasks.webhooks.async_task"):
                    result = send_webhook_task(**kwargs)

                delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                self.assertEqual(delivery.response_code, status_code)
                self.assertIsNotNone(delivery.completed_at)
                self.assertNotIn("private secret", delivery.error_message)
                self.assertNotIn(self.endpoint.url, delivery.error_message)
        self.assertEqual(Schedule.objects.filter(func="core.tasks.send_webhook_task").count(), 0)

    def test_success_replay_is_a_noop_and_does_not_change_attempt(self):
        kwargs = self._task_kwargs()
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            send_webhook_task(**kwargs)
            replay = send_webhook_task(**kwargs)

        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        self.assertEqual(replay.disposition.value, "noop")
        self.assertEqual(request_pinned.call_count, 1)
        self.assertEqual(WebhookDelivery._base_manager.filter(delivery_id=kwargs["delivery_id"]).count(), 1)
        self.assertEqual(delivery.attempt, 1)

    def test_pending_replay_uses_same_record_and_attempt_accounting(self):
        delivery_id = str(uuid4())
        WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=delivery_id,
            status=WebhookDelivery.STATUS_PENDING,
        )
        kwargs = self._task_kwargs(delivery_id=delivery_id)
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
        with patch("django_q.tasks.async_task") as async_task:
            process_event_rules(self.event, self.tenant.pk)
            process_event_rules(legacy_event, self.tenant.pk)

        endpoint_delivery = WebhookDelivery._base_manager.get(event=self.event)
        legacy_delivery = WebhookDelivery._base_manager.get(event=legacy_event)
        self.assertEqual(endpoint_delivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(endpoint_delivery.endpoint_id, self.endpoint.pk)
        self.assertEqual(endpoint_delivery.tenant_id, self.tenant.pk)
        self.assertIsNone(legacy_delivery.endpoint_id)
        self.assertEqual(legacy_delivery.tenant_id, self.tenant.pk)
        self.assertEqual(async_task.call_count, 2)
        endpoint_rule.delete()
        legacy_rule.delete()

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
        )
        with patch("core.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(source.pk, actor_id=self.actor.pk)

        source.refresh_from_db()
        self.assertNotEqual(redelivery.delivery_id, source.delivery_id)
        self.assertEqual(redelivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(redelivery.redelivered_from_id, source.pk)
        self.assertEqual(redelivery.redelivered_by_id, self.actor.pk)
        self.assertIsNotNone(redelivery.redelivered_at)
        self.assertEqual(source.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(source.attempt, 2)
        _, task_kwargs = async_task.call_args
        self.assertEqual(task_kwargs["delivery_id"], redelivery.delivery_id)
        self.assertEqual(task_kwargs["secret"], "")
        self.assertNotIn(self.endpoint.secret_decrypted, repr(task_kwargs))

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
        with patch("core.tasks.webhooks.async_task") as async_task:
            delivery = send_webhook_test(self.endpoint.pk, actor_id=self.actor.pk)
            _, task_kwargs = async_task.call_args

        self.assertTrue(delivery.test_send)
        self.assertIsNone(delivery.event_id)
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_PENDING)
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            send_webhook_task(**task_kwargs)
        payload = json.loads(request_pinned.call_args.kwargs["data"])
        delivery.refresh_from_db()
        self.assertEqual(payload["event_id"], None)
        self.assertEqual(payload["event"], "test")
        self.assertEqual(payload["model"], "extras.WebhookEndpoint")
        self.assertEqual(payload["object_id"], self.endpoint.pk)
        self.assertEqual(payload["data"], {})
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_SUCCESS)

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
                kwargs = self._task_kwargs(headers={"Authorization": secret}, secret="")
                if isinstance(failure, requests.HTTPError):
                    http_response._content = response_body.encode()
                with patch("core.http.request_pinned", side_effect=failure), patch("core.tasks.webhooks.async_task"):
                    send_webhook_task(**kwargs)
                delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
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
                delivery_id = str(uuid4())
                WebhookDelivery._base_manager.create(
                    tenant=self.tenant,
                    endpoint=self.endpoint,
                    event=self.event,
                    delivery_id=delivery_id,
                    status=WebhookDelivery.STATUS_FAILED,
                )
                with patch("core.http.request_pinned") as request_pinned:
                    result = send_webhook_task(**self._task_kwargs(delivery_id=delivery_id, **overrides))
                delivery = WebhookDelivery._base_manager.get(delivery_id=delivery_id)
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                request_pinned.assert_not_called()

    def test_invalid_targets_fail_closed(self):
        cases = (
            {"webhook_endpoint_id": 999999},
            {"webhook_endpoint_id": None, "url": "", "secret": ""},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                kwargs = self._task_kwargs(**overrides)
                with patch("core.http.request_pinned") as request_pinned:
                    result = send_webhook_task(**kwargs)
                delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
                self.assertEqual(result.disposition.value, "terminal")
                self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)
                request_pinned.assert_not_called()

        self.endpoint.enabled = False
        self.endpoint.save(update_fields=["enabled"])
        with patch("core.http.request_pinned") as request_pinned:
            result = send_webhook_task(**self._task_kwargs())
        self.assertEqual(result.disposition.value, "terminal")
        request_pinned.assert_not_called()
        self.endpoint.enabled = True
        self.endpoint.save(update_fields=["enabled"])

        cross_tenant_id = str(uuid4())
        WebhookDelivery._base_manager.create(
            tenant=self.other_tenant,
            endpoint=self.endpoint,
            delivery_id=cross_tenant_id,
            status=WebhookDelivery.STATUS_PENDING,
        )
        with patch("core.http.request_pinned") as request_pinned:
            result = send_webhook_task(**self._task_kwargs(delivery_id=cross_tenant_id))
        self.assertEqual(result.disposition.value, "terminal")
        request_pinned.assert_not_called()

    def test_unknown_event_and_tenant_references_fail_closed(self):
        kwargs = self._task_kwargs(event_id=999999, tenant_id=999999)
        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            result = send_webhook_task(**kwargs)
        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        self.assertTrue(result)
        self.assertIsNone(delivery.event_id)
        self.assertIsNone(delivery.tenant_id)
        payload = json.loads(request_pinned.call_args.kwargs["data"])
        self.assertIsNone(payload["tenant"])

    def test_broken_actor_permission_guard_fails_closed(self):
        from types import SimpleNamespace

        from core.tasks.webhooks import _is_platform_actor

        broken = SimpleNamespace(is_authenticated=True, is_superuser=False)
        self.assertFalse(_is_platform_actor(broken))
        self.assertFalse(_is_platform_actor(None))

    def test_finish_is_noop_when_record_turns_terminal_mid_flight(self):
        kwargs = self._task_kwargs()

        def _mutate(response):
            WebhookDelivery._base_manager.filter(delivery_id=kwargs["delivery_id"]).update(status="dead")
            return response

        with patch("core.http.request_pinned", side_effect=_mutate):
            result = send_webhook_task(**kwargs)
        self.assertEqual(result.disposition.value, "noop")
        delivery = WebhookDelivery._base_manager.get(delivery_id=kwargs["delivery_id"])
        self.assertEqual(delivery.status, WebhookDelivery.STATUS_DEAD)

    def test_slack_and_teams_test_send_carries_test_fields(self):
        for host in ("https://hooks.slack.com/services/test", "https://tenant.webhook.office.com/webhookb2/test"):
            with self.subTest(host=host):
                endpoint = WebhookEndpoint._base_manager.create(
                    name=f"Chat hook {host[:20]}",
                    url=host,
                    tenant=self.tenant,
                )
                with patch("core.tasks.webhooks.async_task") as async_task:
                    delivery = send_webhook_test(endpoint.pk, actor_id=self.actor.pk)
                    _, task_kwargs = async_task.call_args
                with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
                    send_webhook_task(**task_kwargs)
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
        )
        with patch("core.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(test_delivery.pk, actor_id=self.actor.pk)
        _, task_kwargs = async_task.call_args
        self.assertTrue(redelivery.test_send)
        self.assertTrue(task_kwargs["test_send"])
        self.assertEqual(task_kwargs["event_action"], "test")
        self.assertIsNone(task_kwargs["event_id"])

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
        )
        with patch("core.tasks.webhooks.async_task") as async_task:
            redeliver_webhook_delivery(legacy_delivery.pk, actor_id=self.actor.pk)
        _, task_kwargs = async_task.call_args
        self.assertEqual(task_kwargs["url"], "http://8.8.8.8/legacy")
        self.assertIsNone(task_kwargs["webhook_endpoint_id"])

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
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
        )
        with patch("core.tasks.webhooks.async_task") as async_task:
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
        with patch("core.tasks.webhooks.async_task"):
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


@pytest.mark.serial_only
class WebhookDeliveryMigrationTests(TransactionTestCase):
    """Migration 0109 creates only the durable table and reverses cleanly."""

    migrate_from = ("extras", "0108_alertlog_delivery_outcome")
    migrate_to = ("extras", "0109_webhookdelivery")

    def setUp(self):
        super().setUp()
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
