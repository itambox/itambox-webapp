"""Migration rehearsal from itambox/extras/tests/test_webhook_delivery_state_machine.py — run explicitly:

PYTHONPATH=itambox pytest scripts/qualification/migrations/
"""

import ast
import importlib
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations.special import RunPython

from core.events import DeliveryDisposition
from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests
from extras.models import WebhookDelivery
from extras.tasks.webhooks import (
    _decrypt_target_secret,
    send_webhook_task,
)

User = get_user_model()


@isolate_migration_tests
@pytest.mark.serial_only
class WebhookDeliveryMigrationTests(IsolatedMigrationTestCase):
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
        self.assertFalse(any((isinstance(operation, RunPython) for operation in migration_module.Migration.operations)))
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


@isolate_migration_tests
@pytest.mark.serial_only
class WebhookDeliveryTargetMigrationTests(IsolatedMigrationTestCase):
    """Endpoint-backed history gains snapshots; ambiguous legacy rows stay inert."""

    migrate_from = ("extras", "0110_issue445_task_paths")
    migrate_to = ("extras", "0112_backfill_webhookdelivery_targets")

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
            endpoint_id=plaintext_endpoint.pk, delivery_id=str(uuid4()), status="pending"
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


@isolate_migration_tests
@pytest.mark.serial_only
class WebhookRetryScheduleMigrationTests(IsolatedMigrationTestCase):
    """Legacy delayed retries become assertion-only schedules with no target secrets."""

    migrate_from = ("extras", "0112_backfill_webhookdelivery_targets")
    migrate_to = ("extras", "0113_upgrade_legacy_webhook_retry_schedules")

    def setUp(self):
        super().setUp()

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
        delivery = Delivery.objects.create(delivery_id=str(uuid4()), status="failed", test_send=True)
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
        with self.assertRaisesRegex(RuntimeError, "^issue445\\.webhook_retry_upgrade\\.reverse_refused$"):
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
        with self.assertRaisesRegex(RuntimeError, "^issue445\\.webhook_retry_upgrade\\."):
            self._migrate(self.migrate_to)
        HistoricalSchedule.objects.filter(name="Malformed legacy webhook retry").delete()
