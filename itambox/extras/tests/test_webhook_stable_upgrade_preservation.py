"""Upgrade preservation for the Webhooks & Event Rules Stable promotion.

The promotion adds no migration and must not rewrite, reinterpret, or re-deliver
anything an operator configured on the supported Beta (including ``beta.3``):
an upgraded deployment keeps its endpoints, rules, and delivery history exactly
as they were, and existing enabled objects keep working unchanged.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import TenantTestMixin
from extras.models import Event, EventRule, WebhookDelivery, WebhookEndpoint
from extras.services.events import dispatch_event
from extras.tasks.webhooks import (
    recover_pending_webhook_deliveries,
    redeliver_webhook_delivery,
    send_webhook_task,
)
from organization.models import Location, Tenant

User = get_user_model()


class WebhookUpgradePreservationTests(TenantTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="Upgrade tenant", slug="upgrade-tenant")
        self.actor = User.objects.create_superuser(
            username="upgrade-admin",
            email="upgrade-admin@example.com",
            password="password",
        )
        self.location_ct = ContentType.objects.get_for_model(Location)
        self.endpoint = WebhookEndpoint._base_manager.create(
            name="Upgrade endpoint",
            url="https://example.com/upgrade-hook",
            tenant=self.tenant,
            secret="upgrade-endpoint-secret",
            retry_count=3,
            retry_backoff=60,
        )
        self.event = Event.objects.create(
            model=self.location_ct,
            object_id=111111,
            action=Event.ACTION_CREATE,
            data={"app_label": "organization", "model_name": "location"},
        )
        now = timezone.now()
        target = {
            "target_url": self.endpoint.url,
            "target_http_method": self.endpoint.http_method,
            "target_headers": dict(self.endpoint.headers),
            "target_secret": self.endpoint.secret,
            "target_enabled": True,
            "target_tenant_id": self.endpoint.tenant_id,
            "target_retry_count": self.endpoint.retry_count,
            "target_retry_backoff": self.endpoint.retry_backoff,
        }
        self.successful = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_SUCCESS,
            response_code=200,
            attempt=1,
            payload_timestamp=now - timezone.timedelta(hours=3),
            completed_at=now - timezone.timedelta(hours=3),
            **target,
        )
        self.failed = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_FAILED,
            response_code=503,
            error_class="integration.unavailable",
            error_message="Webhook delivery is temporarily unavailable.",
            attempt=2,
            payload_timestamp=now - timezone.timedelta(hours=1),
            next_retry_at=now + timezone.timedelta(hours=1),
            **target,
        )
        self.dead = WebhookDelivery._base_manager.create(
            tenant=self.tenant,
            endpoint=self.endpoint,
            event=self.event,
            delivery_id=str(uuid4()),
            status=WebhookDelivery.STATUS_DEAD,
            response_code=422,
            error_class="integration.request_rejected",
            error_message="Webhook delivery was rejected.",
            attempt=1,
            payload_timestamp=now - timezone.timedelta(hours=2),
            completed_at=now - timezone.timedelta(hours=2),
            **target,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)
        super().tearDown()

    @staticmethod
    def _snapshot(row):
        return {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}

    @staticmethod
    def _response(status_code=200):
        response = MagicMock(status_code=status_code)
        response.raise_for_status.return_value = None
        return response

    def test_upgrade_preserves_history_and_backlog_stays_quiet(self):
        rows = (self.successful, self.failed, self.dead)
        before = {row.pk: self._snapshot(row) for row in rows}

        with (
            patch("core.http.request_pinned") as request_pinned,
            patch("extras.tasks.webhooks.async_task") as async_task,
        ):
            recovered = recover_pending_webhook_deliveries()

        self.assertEqual(recovered, {"dispatched": 0})
        request_pinned.assert_not_called()
        async_task.assert_not_called()

        self.assertEqual(
            WebhookDelivery._base_manager.filter(tenant=self.tenant).count(),
            len(rows),
        )
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(self._snapshot(row), before[row.pk])

    def test_enabled_objects_keep_delivering_without_auto_activation(self):
        disabled_endpoint = WebhookEndpoint._base_manager.create(
            name="Upgrade disabled endpoint",
            url="https://example.com/upgrade-disabled-hook",
            tenant=self.tenant,
        )
        EventRule.objects.create(
            name="Upgrade enabled rule",
            model=self.location_ct,
            events=[Event.ACTION_CREATE],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=self.endpoint,
            tenant=self.tenant,
            enabled=True,
        )
        EventRule.objects.create(
            name="Upgrade disabled rule",
            model=self.location_ct,
            events=[Event.ACTION_CREATE],
            action_type=EventRule.ACTION_WEBHOOK,
            webhook=disabled_endpoint,
            tenant=self.tenant,
            enabled=False,
        )
        location = Location(name="Upgrade location", tenant=self.tenant)
        location.pk = 222222

        with patch("core.http.request_pinned", return_value=self._response()) as request_pinned:
            with transaction.atomic():
                dispatch_event(Location, location, Event.ACTION_CREATE)

        self.assertEqual(request_pinned.call_count, 1)
        fresh = WebhookDelivery._base_manager.get(
            event__model=self.location_ct,
            event__object_id=location.pk,
            event__action=Event.ACTION_CREATE,
        )
        self.assertEqual(fresh.endpoint_id, self.endpoint.pk)
        self.assertEqual(fresh.status, WebhookDelivery.STATUS_SUCCESS)
        self.assertEqual(
            WebhookDelivery._base_manager.filter(endpoint=disabled_endpoint).count(),
            0,
        )

    def test_redelivery_of_preserved_history_still_works(self):
        source_before = self._snapshot(self.dead)
        with patch("extras.tasks.webhooks.async_task") as async_task:
            redelivery = redeliver_webhook_delivery(self.dead.pk, actor_id=self.actor.pk)
        task_args, task_kwargs = async_task.call_args
        self.assertEqual(redelivery.status, WebhookDelivery.STATUS_PENDING)
        self.assertEqual(redelivery.redelivered_from_id, self.dead.pk)
        self.assertNotEqual(redelivery.delivery_id, self.dead.delivery_id)

        with patch("core.http.request_pinned", return_value=self._response()):
            result = send_webhook_task(task_args[1], **task_kwargs)
        self.assertEqual(result.disposition.value, "success")
        redelivery.refresh_from_db()
        self.assertEqual(redelivery.status, WebhookDelivery.STATUS_SUCCESS)
        self.dead.refresh_from_db()
        self.assertEqual(self._snapshot(self.dead), source_before)
