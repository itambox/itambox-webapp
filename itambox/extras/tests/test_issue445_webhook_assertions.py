"""RED exact-parity tests for issue #445 durable webhook identity assertions."""

import importlib
import logging
from types import SimpleNamespace
from unittest import mock
from uuid import UUID, uuid4

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from django_q.models import Schedule

from assets.models import Manufacturer
from core.events import DeliveryResult
from extras.models import Event, EventRule, WebhookDelivery, WebhookEndpoint
from organization.models import Tenant

MISMATCH_CASES = (
    "omitted_object",
    "omitted_pk",
    "invalid_pk",
    "omitted_uuid",
    "invalid_uuid",
    "wrong_pk_other_uuid",
    "wrong_uuid",
    "omitted_endpoint",
    "wrong_endpoint",
    "omitted_event",
    "wrong_event",
    "omitted_tenant",
    "wrong_tenant",
    "wrong_claims_against_nulls",
    "omitted_test_send",
    "inverted_test_send",
    "string_test_send",
    "string_endpoint_id",
)


def _webhook_module():
    try:
        return importlib.import_module("extras.tasks.webhooks")
    except ImportError:
        return importlib.import_module("extras.tasks.webhooks")


def _assertion_type(module):
    assertion_type = getattr(module, "WebhookDeliveryAssertions", None)
    assert assertion_type is not None, "missing issue445 immutable WebhookDeliveryAssertions exact-parity contract"
    return assertion_type


def _snapshot(delivery):
    return {field.attname: getattr(delivery, field.attname) for field in delivery._meta.concrete_fields}


class Issue445WebhookAssertionTests(TestCase):
    """RED at base: the current task accepts mutable, partial identity claims."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Issue445 webhook tenant", slug="issue445-webhook-tenant")
        self.other_tenant = Tenant.objects.create(name="Issue445 other tenant", slug="issue445-webhook-other")
        self.endpoint = WebhookEndpoint._base_manager.create(
            name="Issue445 endpoint",
            tenant=self.tenant,
            url="https://8.8.8.8/issue445-private-url",
            headers={"Authorization": "issue445-private-header"},
            secret="issue445-private-secret",
        )
        self.other_endpoint = WebhookEndpoint._base_manager.create(
            name="Issue445 other endpoint",
            tenant=self.tenant,
            url="https://1.1.1.1/other-hook",
        )
        self.event = Event.objects.create(
            model=ContentType.objects.get_for_model(Manufacturer),
            object_id=445,
            action=Event.ACTION_CREATE,
            data={"canary": "issue445-private-payload"},
        )
        self.other_event = Event.objects.create(
            model=self.event.model,
            object_id=446,
            action=Event.ACTION_UPDATE,
            data={},
        )

    def _delivery(self, *, status=WebhookDelivery.STATUS_PENDING, nullable=False, test_send=False):
        return WebhookDelivery._base_manager.create(
            tenant=None if nullable else self.tenant,
            endpoint=None if nullable else self.endpoint,
            event=None if nullable else self.event,
            delivery_id=str(uuid4()),
            status=status,
            attempt=2,
            error_class="safe.prior" if status == WebhookDelivery.STATUS_FAILED else "",
            error_message="safe prior" if status == WebhookDelivery.STATUS_FAILED else "",
            test_send=test_send,
            target_url=self.endpoint.url,
            target_http_method=self.endpoint.http_method,
            target_headers=self.endpoint.headers,
            target_secret=self.endpoint.secret,
            target_enabled=True,
            target_tenant_id=None if nullable else self.endpoint.tenant_id,
            target_retry_count=self.endpoint.retry_count,
            target_retry_backoff=self.endpoint.retry_backoff,
        )

    @staticmethod
    def _values(delivery):
        return {
            "delivery_pk": delivery.pk,
            "delivery_id": UUID(delivery.delivery_id),
            "webhook_endpoint_id": delivery.endpoint_id,
            "event_id": delivery.event_id,
            "tenant_id": delivery.tenant_id,
            "test_send": delivery.test_send,
        }

    def _assertions_for_case(self, module, case, delivery):
        if case == "omitted_object":
            return None
        values = self._values(delivery)
        assertion_type = _assertion_type(module)
        mutations = {
            "omitted_pk": lambda: values.pop("delivery_pk"),
            "invalid_pk": lambda: values.update(delivery_pk="not-an-integer"),
            "omitted_uuid": lambda: values.pop("delivery_id"),
            "invalid_uuid": lambda: values.update(delivery_id="not-a-uuid"),
            "wrong_pk_other_uuid": lambda: self._replace_with_other_delivery(values),
            "wrong_uuid": lambda: values.update(delivery_id=uuid4()),
            "omitted_endpoint": lambda: values.pop("webhook_endpoint_id"),
            "wrong_endpoint": lambda: values.update(webhook_endpoint_id=self.other_endpoint.pk),
            "omitted_event": lambda: values.pop("event_id"),
            "wrong_event": lambda: values.update(event_id=self.other_event.pk),
            "omitted_tenant": lambda: values.pop("tenant_id"),
            "wrong_tenant": lambda: values.update(tenant_id=self.other_tenant.pk),
            "wrong_claims_against_nulls": lambda: values.update(
                webhook_endpoint_id=self.endpoint.pk, event_id=self.event.pk, tenant_id=self.tenant.pk
            ),
            "omitted_test_send": lambda: values.pop("test_send"),
            "inverted_test_send": lambda: values.update(test_send=not delivery.test_send),
            "string_test_send": lambda: values.update(test_send=str(delivery.test_send).lower()),
            "string_endpoint_id": lambda: values.update(webhook_endpoint_id=str(delivery.endpoint_id)),
        }
        mutations[case]()

        try:
            return assertion_type(**values)
        except TypeError:
            # Omitted fields must reach the task boundary as malformed input,
            # not disappear from this matrix because dataclass construction is strict.
            return SimpleNamespace(**values)

    def _replace_with_other_delivery(self, values):
        other = self._delivery(nullable=True, test_send=True)
        values.update(delivery_pk=other.pk, delivery_id=UUID(other.delivery_id))

    def _invoke_rejection(self, case, *, status=WebhookDelivery.STATUS_PENDING):
        module = _webhook_module()
        _assertion_type(module)
        nullable = case == "wrong_claims_against_nulls"
        delivery = self._delivery(status=status, nullable=nullable, test_send=case == "inverted_test_send")
        assertions = self._assertions_for_case(module, case, delivery)
        before = {item.pk: _snapshot(item) for item in WebhookDelivery._base_manager.order_by("pk")}
        before_count = WebhookDelivery._base_manager.count()

        transport = mock.patch.object(
            module,
            "_dispatch_webhook_request",
            side_effect=AssertionError("identity rejection touched HTTP transport"),
        )
        scheduler = mock.patch.object(module, "async_task", side_effect=AssertionError("identity rejection retried"))
        delayed_retry = mock.patch.object(
            Schedule.objects,
            "create",
            side_effect=AssertionError("identity rejection scheduled a retry"),
        )
        dns = mock.patch("core.http.webhook_target_kind", side_effect=AssertionError("identity rejection touched DNS"))
        endpoint_lookup = mock.patch.object(
            WebhookEndpoint._base_manager,
            "filter",
            side_effect=AssertionError("identity rejection resolved an endpoint relation"),
        )
        event_lookup = mock.patch.object(
            Event._base_manager,
            "filter",
            side_effect=AssertionError("identity rejection resolved an event relation"),
        )
        with transport, scheduler, delayed_retry, dns, endpoint_lookup, event_lookup:
            result = module.send_webhook_task(assertions)

        self.assertIsInstance(result, DeliveryResult, "missing issue445 typed terminal webhook rejection contract")
        self.assertEqual(result.disposition.value, "terminal", "missing issue445 terminal webhook rejection contract")
        safe_code = result.error_class
        self.assertTrue(safe_code, "missing issue445 stable sanitized webhook rejection code")
        self.assertNotIn("issue445-private", repr(result), "missing issue445 secret-free rejection result contract")
        self.assertEqual(
            WebhookDelivery._base_manager.count(),
            before_count,
            "missing issue445 assertion rejection must not create a delivery row",
        )
        self.assertEqual(
            {item.pk: _snapshot(item) for item in WebhookDelivery._base_manager.order_by("pk")},
            before,
            "missing issue445 byte-equivalent delivery-row rejection contract",
        )
        safe_ids = []
        supplied_pk = getattr(assertions, "delivery_pk", None)
        supplied_uuid = getattr(assertions, "delivery_id", None)
        if isinstance(supplied_pk, int):
            safe_ids.append(str(supplied_pk))
        if isinstance(supplied_uuid, UUID):
            safe_ids.append(str(supplied_uuid))
        return safe_code, safe_ids

    def test_every_omission_and_mismatch_rejects_before_side_effects(self):
        for case in MISMATCH_CASES:
            with self.subTest(case=case), self.assertLogs(level=logging.WARNING) as captured:
                safe_code, safe_ids = self._invoke_rejection(case)
            audit = [record for record in captured.records if safe_code in record.getMessage()]
            self.assertEqual(len(audit), 1, "missing issue445 one-record sanitized webhook security audit contract")
            rendered = audit[0].getMessage()
            for safe_id in safe_ids:
                self.assertIn(safe_id, rendered, "missing issue445 safe webhook audit identity")
            for secret in (
                "issue445-private-url",
                "issue445-private-header",
                "issue445-private-secret",
                "issue445-private-payload",
                "Authorization",
            ):
                self.assertNotIn(secret, rendered, "missing issue445 secret-free webhook security audit contract")
            self.assertIsNone(audit[0].exc_info, "missing issue445 traceback-free webhook security audit contract")

    def test_mismatch_precedes_every_canonical_status_branch(self):
        for status in (
            WebhookDelivery.STATUS_PENDING,
            WebhookDelivery.STATUS_FAILED,
            WebhookDelivery.STATUS_SUCCESS,
            WebhookDelivery.STATUS_DEAD,
        ):
            with self.subTest(status=status), self.assertLogs(level=logging.WARNING):
                self._invoke_rejection("wrong_uuid", status=status)

    def test_exact_none_claims_and_false_true_test_send_are_accepted(self):
        module = _webhook_module()
        assertion_type = _assertion_type(module)
        legacy_rule = EventRule.objects.create(
            name="Issue445 legacy exact-None rule",
            tenant=self.tenant,
            model=self.event.model,
            events=[self.event.action],
            action_type=EventRule.ACTION_WEBHOOK,
            action_config={"url": "https://legacy.example.invalid/hook"},
        )
        deliveries = (
            self._delivery(test_send=False),
            WebhookDelivery._base_manager.create(
                tenant=self.tenant,
                endpoint=self.endpoint,
                event=None,
                delivery_id=str(uuid4()),
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
            ),
            WebhookDelivery._base_manager.create(
                tenant=self.tenant,
                endpoint=None,
                event=self.event,
                delivery_id=str(uuid4()),
                test_send=False,
                event_rule_id=legacy_rule.pk,
                target_url="https://legacy.example.invalid/hook",
                target_http_method="POST",
                target_headers={},
                target_secret="",
                target_enabled=True,
                target_tenant_id=None,
                target_retry_count=3,
                target_retry_backoff=60,
            ),
        )
        response = mock.MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        with mock.patch.object(module, "_dispatch_webhook_request", return_value=response) as transport:
            for delivery in deliveries:
                with self.subTest(delivery=delivery.pk, test_send=delivery.test_send):
                    assertions = assertion_type(**self._values(delivery))
                    result = module.send_webhook_task(assertions)
                    self.assertTrue(
                        result,
                        "missing issue445 exact None==None and false/true test_send acceptance contract",
                    )
        self.assertEqual(transport.call_count, len(deliveries))
