import ast
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone
from django_q.models import Schedule

from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import TenantTestMixin
from extras.models import Event, EventRule, WebhookDelivery, WebhookEndpoint
from extras.services.events import dispatch_event
from extras.tasks.webhooks import redeliver_webhook_delivery, send_webhook_task
from organization.models import Location, Tenant

User = get_user_model()

V1_EVENT_ACTIONS = {"create", "update", "delete", "restore", "checkout", "checkin"}
V1_ENVELOPE_MEMBERS = {
    "schema_version",
    "event_id",
    "delivery_id",
    "attempt",
    "tenant",
    "event",
    "model",
    "object_id",
    "timestamp",
    "data",
}


class WebhookReceiverContractTests(TenantTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="Receiver contract tenant", slug="receiver-contract-tenant")
        self.actor = User.objects.create_superuser(
            username="receiver-contract-admin",
            email="receiver-contract-admin@example.com",
            password="password",
        )
        self.endpoint = WebhookEndpoint._base_manager.create(
            name="Receiver contract endpoint",
            url="https://example.com/receiver-contract",
            tenant=self.tenant,
            secret="receiver-contract-secret",
            retry_count=2,
            retry_backoff=60,
        )
        self.location_ct = ContentType.objects.get_for_model(Location)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)
        super().tearDown()

    @staticmethod
    def _response(status_code=200, response_text=""):
        response = MagicMock(status_code=status_code)
        response.raise_for_status.return_value = None
        response.text = response_text
        if status_code >= 500:
            response.raise_for_status.side_effect = requests.HTTPError(response=response)
        return response

    def test_receiver_contract_survives_retry_and_manual_redelivery(self):
        response_text = "receiver response body must not be persisted"
        responses = iter(
            (
                self._response(503, response_text=response_text),
                self._response(200),
                self._response(200),
            )
        )
        receiver_calls = []

        def receive_request(method, url, **kwargs):
            receiver_calls.append(
                {
                    "method": method,
                    "url": url,
                    "headers": dict(kwargs["headers"]),
                    "body": kwargs["data"],
                }
            )
            return next(responses)

        EventRule.objects.create(
            name="Receiver contract rule",
            model=self.location_ct,
            events=[Event.ACTION_CREATE],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=self.endpoint,
            tenant=self.tenant,
            enabled=True,
        )
        location = Location(name="Receiver contract location", tenant=self.tenant)
        location.pk = 987654

        with (
            patch("core.http.request_pinned", side_effect=receive_request) as request_pinned,
            patch("extras.tasks.webhooks.random.uniform", return_value=1.0),
        ):
            with transaction.atomic():
                dispatch_event(Location, location, Event.ACTION_CREATE)

            event = Event._base_manager.get(
                model=self.location_ct,
                object_id=location.pk,
                action=Event.ACTION_CREATE,
            )
            source = WebhookDelivery._base_manager.get(event=event, endpoint=self.endpoint)
            self.assertEqual(source.status, WebhookDelivery.STATUS_FAILED)
            self.assertEqual(source.error_class, "integration.unavailable")
            self.assertEqual(source.response_code, 503)
            self.assertIsNotNone(source.next_retry_at)
            self.assertGreater(source.next_retry_at, timezone.now())
            self.assertNotIn(response_text, source.error_message)

            retry_schedule = Schedule.objects.filter(func="extras.tasks.webhooks.send_webhook_task").latest("pk")
            retry_kwargs = ast.literal_eval(retry_schedule.kwargs)
            with patch("extras.tasks.webhooks.timezone.now", return_value=source.next_retry_at):
                retry_result = send_webhook_task(**retry_kwargs)
            self.assertEqual(retry_result.disposition.value, "success")
            source.refresh_from_db()
            self.assertEqual(source.status, WebhookDelivery.STATUS_SUCCESS)

            source_state = (
                source.delivery_id,
                source.status,
                source.attempt,
                source.response_code,
                source.error_class,
                source.error_message,
                source.next_retry_at,
                source.completed_at,
            )
            with patch("extras.tasks.webhooks.async_task") as async_task:
                redelivery = redeliver_webhook_delivery(source.pk, actor_id=self.actor.pk)
            task_args, task_kwargs = async_task.call_args
            self.assertNotEqual(redelivery.delivery_id, source.delivery_id)
            self.assertEqual(redelivery.redelivered_from_id, source.pk)
            self.assertEqual(redelivery.status, WebhookDelivery.STATUS_PENDING)
            redelivery_result = send_webhook_task(task_args[1], **task_kwargs)
            self.assertEqual(redelivery_result.disposition.value, "success")

            source.refresh_from_db()
            self.assertEqual(
                (
                    source.delivery_id,
                    source.status,
                    source.attempt,
                    source.response_code,
                    source.error_class,
                    source.error_message,
                    source.next_retry_at,
                    source.completed_at,
                ),
                source_state,
            )
            self.assertEqual(request_pinned.call_count, 3)

        self.assertEqual(len(receiver_calls), 3)
        payloads = []
        for call in receiver_calls:
            self.assertEqual(call["method"], "POST")
            self.assertEqual(call["url"], self.endpoint.url)
            raw_body = call["body"]
            payload = json.loads(raw_body)
            self.assertEqual(set(payload), V1_ENVELOPE_MEMBERS)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["event"], Event.ACTION_CREATE)
            self.assertIn(payload["event"], V1_EVENT_ACTIONS)

            signature = call["headers"]["X-Hub-Signature-256"]
            self.assertTrue(signature.startswith("sha256="))
            expected_signature = hmac.new(
                self.endpoint.secret_decrypted.encode("utf-8"),
                raw_body.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            self.assertTrue(hmac.compare_digest(signature, f"sha256={expected_signature}"))
            payloads.append(payload)

        first, retry, redelivered = payloads
        self.assertEqual([payload["attempt"] for payload in payloads], [1, 2, 1])
        self.assertEqual(first["event_id"], retry["event_id"])
        self.assertEqual(first["delivery_id"], retry["delivery_id"])
        self.assertNotEqual(first["delivery_id"], redelivered["delivery_id"])
        self.assertEqual(first["event_id"], redelivered["event_id"])
        receiver_dedupe_keys = {
            (payload["event_id"], payload["delivery_id"], payload["attempt"]) for payload in payloads
        }
        self.assertEqual(len(receiver_dedupe_keys), 3)
