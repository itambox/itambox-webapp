"""WP-13 (#185): truthful, observable alert-channel delivery failure semantics.

Path B contract: exactly one delivery attempt per planned dispatch; typed
per-channel outcomes persisted in ``delivery_status``; filterable
``delivery_outcome``; stable unique delivery ids with idempotent repeated
invocation; attempt counter and typed failure queryable in-product; Stable
inbox lifecycle independent of delivery success; explicit absence of manual
redelivery (nothing to advertise in UI/API).
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.events import DeliveryDisposition, DeliveryResult
from core.models import Notification
from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests
from core.tests.mixins import TenantTestMixin
from extras.filters import AlertLogFilterSet
from extras.models import AlertLog, AlertRule, NotificationChannel
from extras.tables import AlertLogTable
from extras.tasks.alerts import (
    _delivery_error,
    _delivery_outcome,
    _dispatch_channels,
    _evaluate_rule,
    _schedule_alert_dispatch,
)
from organization.models import Tenant

User = get_user_model()


class DeliveryOutcomeDerivationTests(SimpleTestCase):
    """Pure derivation helpers: filterable outcome + typed error from payloads."""

    def test_empty_payload_is_none(self):
        self.assertEqual(_delivery_outcome({}), AlertLog.DELIVERY_OUTCOME_NONE)
        self.assertEqual(_delivery_outcome(None), AlertLog.DELIVERY_OUTCOME_NONE)
        self.assertIsNone(_delivery_error({}))
        self.assertIsNone(_delivery_error(None))

    def test_pending_and_crash_markers_are_respected(self):
        self.assertEqual(
            _delivery_outcome({"__dispatch__": "pending"}),
            AlertLog.DELIVERY_OUTCOME_PENDING,
        )
        self.assertEqual(
            _delivery_outcome({"__dispatch__": "terminal"}),
            AlertLog.DELIVERY_OUTCOME_FAILED,
        )
        self.assertEqual(_delivery_error({"__dispatch__": "terminal"}), "dispatch_crash")
        self.assertIsNone(_delivery_error({"__dispatch__": "pending"}))

    def test_no_channels_payload_is_none(self):
        payload = {"__no_channels__": "no channels attached to this rule"}
        self.assertEqual(_delivery_outcome(payload), AlertLog.DELIVERY_OUTCOME_NONE)
        self.assertIsNone(_delivery_error(payload))

    def test_legacy_string_payloads_derive_truthfully(self):
        self.assertEqual(_delivery_outcome({"7": "ok"}), AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertEqual(_delivery_outcome({"7": "failed"}), AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(_delivery_outcome({"7": "error: SMTP rejected"}), AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(_delivery_outcome({"7": "retryable"}), AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(_delivery_error({"7": "retryable"}), "retryable")
        self.assertIsNone(_delivery_error({"7": "ok"}))

    def test_structured_payloads_derive_truthfully(self):
        delivered = {
            "7": {"disposition": "success", "operation": "in_app.deliver", "delivery_id": "run-1"},
        }
        failed = {
            "7": {
                "disposition": "terminal",
                "operation": "email.deliver",
                "delivery_id": "run-1",
                "error_class": "SMTPException",
            },
        }
        mixed = {
            "7": {"disposition": "success", "operation": "in_app.deliver"},
            "9": {"disposition": "terminal", "operation": "slack.deliver", "error_class": "http_4xx"},
        }
        self.assertEqual(_delivery_outcome(delivered), AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertEqual(_delivery_outcome(failed), AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(_delivery_outcome(mixed), AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertEqual(_delivery_error(failed), "SMTPException")
        self.assertEqual(_delivery_error(mixed), "http_4xx")
        self.assertIsNone(_delivery_error(delivered))

    def test_structured_retryable_failure_is_failed_and_typed(self):
        payload = {"7": {"disposition": "retryable", "operation": "slack.deliver", "delivery_id": "run-1"}}
        self.assertEqual(_delivery_outcome(payload), AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(_delivery_error(payload), "retryable")

    def test_unknown_values_are_failed_by_default(self):
        self.assertEqual(_delivery_outcome({"7": "mystery"}), AlertLog.DELIVERY_OUTCOME_FAILED)


class ChannelDeliveryOutcomeTests(TestCase):
    """Typed structured per-channel outcomes recorded by the dispatch boundary."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="WP-13 Channel Tenant", slug="wp-13-channel-tenant")
        self.user = User.objects.create_user(username="wp13-channel", password="x")
        self.rule = AlertRule.objects.create(
            name="WP-13 Channel Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
            tenant=self.tenant,
        )
        self.match = {"subject": "WP-13", "message": "body", "tenant": self.tenant}

    def _channel(self, channel_type, **config):
        if channel_type == NotificationChannel.TYPE_IN_APP:
            config.setdefault("recipient_users", [self.user.pk])
        channel = NotificationChannel.objects.create(
            name=f"WP-13 {channel_type}",
            channel_type=channel_type,
            tenant=self.tenant,
            enabled=True,
            config=config,
        )
        self.rule.channels.add(channel)
        return channel

    def test_successful_channel_records_structured_entry(self):
        channel = self._channel(NotificationChannel.TYPE_IN_APP)
        delivery = _dispatch_channels(self.rule, self.match, None, delivery_id="run-7")
        entry = delivery[str(channel.pk)]
        self.assertEqual(entry["disposition"], DeliveryDisposition.SUCCESS.value)
        self.assertEqual(entry["operation"], "in_app.deliver")
        self.assertEqual(entry["delivery_id"], "run-7")
        self.assertIn("attempted_at", entry)
        self.assertNotIn("error_class", entry)
        self.assertNotIn("message", entry)

    def test_terminal_failure_records_typed_error_and_safe_message_only(self):
        channel = self._channel(NotificationChannel.TYPE_IN_APP)
        terminal = DeliveryResult(
            "slack.deliver",
            DeliveryDisposition.TERMINAL,
            True,
            "Notification delivery was rejected.",
            "SMTPException",
        )
        with self._patched_sender(terminal):
            delivery = _dispatch_channels(self.rule, self.match, None, delivery_id="run-7")
        entry = delivery[str(channel.pk)]
        self.assertEqual(entry["disposition"], DeliveryDisposition.TERMINAL.value)
        self.assertEqual(entry["error_class"], "SMTPException")
        self.assertEqual(entry["message"], "Notification delivery was rejected.")

    def test_non_user_visible_failure_keeps_no_message(self):
        channel = self._channel(NotificationChannel.TYPE_IN_APP)
        retryable = DeliveryResult("slack.deliver", DeliveryDisposition.RETRYABLE)
        with self._patched_sender(retryable):
            delivery = _dispatch_channels(self.rule, self.match, None, delivery_id="run-7")
        entry = delivery[str(channel.pk)]
        self.assertEqual(entry["disposition"], DeliveryDisposition.RETRYABLE.value)
        self.assertNotIn("message", entry)

    def test_unexpected_exception_becomes_typed_terminal_entry(self):
        channel = self._channel(NotificationChannel.TYPE_IN_APP)
        with self._patched_sender(RuntimeError("backend exploded")):
            delivery = _dispatch_channels(self.rule, self.match, None, delivery_id="run-7")
        entry = delivery[str(channel.pk)]
        self.assertEqual(entry["disposition"], DeliveryDisposition.TERMINAL.value)
        self.assertEqual(entry["error_class"], "unexpected_channel_error")

    def _patched_sender(self, result):
        from unittest.mock import patch

        if isinstance(result, BaseException):
            return patch("extras.tasks.alerts.send_notification_to_channel", side_effect=result)
        return patch("extras.tasks.alerts.send_notification_to_channel", return_value=result)


class AlertDispatchObservabilityTests(TransactionTestCase):
    """Full evaluation loop: attempts, delivery ids, outcomes, idempotency."""

    def _setup(self, renotify_interval_days=0):
        tenant = Tenant.objects.create(name="WP-13 Observability Tenant", slug="wp-13-observability-tenant")
        user = User.objects.create_user(username="wp13-obs", password="x")
        rule = AlertRule.objects.create(
            name="WP-13 Observability Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            tenant=tenant,
            renotify_interval_days=renotify_interval_days,
        )
        channel = NotificationChannel.objects.create(
            name="WP-13 in-app",
            channel_type=NotificationChannel.TYPE_IN_APP,
            tenant=tenant,
            enabled=True,
            config={"recipient_users": [user.pk]},
        )
        rule.channels.add(channel)
        return tenant, user, rule, channel

    def test_full_evaluation_persists_attempts_id_and_outcome(self):
        from unittest.mock import patch

        from assets.models import Manufacturer
        from inventory.models import Accessory, AccessoryStock
        from organization.models import Location, Site

        tenant, _user, rule, channel = self._setup()
        manufacturer = Manufacturer.objects.create(name="WP-13 Mfr", slug="wp-13-mfr")
        site = Site.objects.create(name="WP-13 Site", slug="wp-13-site", tenant=tenant)
        location = Location.objects.create(name="WP-13 Loc", slug="wp-13-loc", tenant=tenant, site=site)
        accessory = Accessory.objects.create(
            name="WP-13 Accessory",
            slug="wp-13-accessory",
            manufacturer=manufacturer,
            tenant=tenant,
            min_qty=5,
        )
        AccessoryStock.objects.create(accessory=accessory, location=location, qty=1)

        with patch("extras.tasks.alerts.uuid4", return_value="run-42"):
            with transaction.atomic():
                from core.tasks.context import TaskContext

                with TaskContext(tenant_id=tenant.pk):
                    _evaluate_rule(rule, timezone.now().date(), {})

        alert = AlertLog.unscoped.get(rule=rule)
        self.assertEqual(alert.delivery_attempts, 1)
        self.assertEqual(alert.last_delivery_id, "run-42")
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertIsNone(alert.last_delivery_error)
        entry = alert.delivery_status[str(channel.pk)]
        self.assertEqual(entry["disposition"], DeliveryDisposition.SUCCESS.value)
        self.assertEqual(alert.delivery_status["__delivery_id__"], "run-42")

    def test_repeated_invocation_of_same_run_is_idempotent(self):
        from unittest.mock import patch

        from core.tasks.context import TaskContext

        tenant, user, rule, channel = self._setup()
        content_type = ContentType.objects.get_for_model(AlertRule)
        alert = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="idem",
            message="idem",
            content_type=content_type,
            object_id=rule.pk,
            delivery_status={"__dispatch__": "pending"},
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_PENDING,
        )
        match = {"obj": rule, "tenant": tenant, "subject": "idem", "message": "idem"}

        # Both scheduled runs claim the SAME delivery id (simulates a replayed
        # on_commit callback): the second invocation must be skipped entirely.
        with patch("extras.tasks.alerts.uuid4", side_effect=["run-1", "run-1"]):
            with TaskContext(tenant_id=tenant.pk):
                with transaction.atomic():
                    _schedule_alert_dispatch(rule, match, alert)
                    _schedule_alert_dispatch(rule, match, alert)

        self.assertEqual(
            Notification.objects.filter(user=user, subject="idem").count(),
            1,
            "replayed delivery run must not duplicate in-app notifications",
        )
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_attempts, 1)
        self.assertEqual(alert.last_delivery_id, "run-1")
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_DELIVERED)

    def test_renotify_starts_fresh_attempt_with_new_id(self):
        from unittest.mock import patch

        from core.tasks.context import TaskContext

        tenant, _user, rule, channel = self._setup(renotify_interval_days=1)
        content_type = ContentType.objects.get_for_model(AlertRule)
        alert = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="renotify",
            message="renotify",
            content_type=content_type,
            object_id=rule.pk,
            delivery_status={"7": "ok"},
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_DELIVERED,
            delivery_attempts=1,
            last_delivery_id="old-run",
            last_notified_at=timezone.now() - timezone.timedelta(days=2),
        )
        match = {"obj": rule, "tenant": tenant, "subject": "renotify", "message": "renotify"}
        existing = {(rule.pk, alert.content_type_id, alert.object_id): alert}

        with (
            patch("extras.tasks.alerts.uuid4", return_value="fresh-run"),
            patch("extras.tasks.alerts._collect_matches", return_value=[match]),
        ):
            with TaskContext(tenant_id=tenant.pk):
                with transaction.atomic():
                    _evaluate_rule(rule, timezone.now().date(), existing)

        alert.refresh_from_db()
        self.assertEqual(alert.delivery_attempts, 2)
        self.assertEqual(alert.last_delivery_id, "fresh-run")
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_DELIVERED)


class AlertDeliveryFilterTests(TestCase):
    def test_delivery_outcome_filter_finds_fired_but_undelivered(self):
        tenant = Tenant.objects.create(name="WP-13 Filter Tenant", slug="wp-13-filter-tenant")
        rule = AlertRule.objects.create(
            name="WP-13 Filter Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
            tenant=tenant,
        )
        content_type = ContentType.objects.get_for_model(AlertRule)
        delivered = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="delivered",
            message="m",
            content_type=content_type,
            object_id=1,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_DELIVERED,
        )
        failed = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="failed",
            message="m",
            content_type=content_type,
            object_id=2,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_FAILED,
        )
        pending = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="pending",
            message="m",
            content_type=content_type,
            object_id=3,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_PENDING,
        )

        filterset = AlertLogFilterSet(
            {"delivery_outcome": [AlertLog.DELIVERY_OUTCOME_FAILED]},
            queryset=AlertLog._base_manager.all(),
        )
        self.assertEqual(set(filterset.qs.values_list("pk", flat=True)), {failed.pk})

        all_outcomes = AlertLogFilterSet({}, queryset=AlertLog._base_manager.all())
        self.assertEqual(
            set(all_outcomes.qs.values_list("pk", flat=True)),
            {delivered.pk, failed.pk, pending.pk},
        )


class AlertDeliveryApiTests(TenantTestMixin, APITestCase):
    permissions = ["extras.view_alertlog"]

    def setUp(self):
        self.setup_tenant_context(
            name="WP-13 API A",
            slug="wp-13-api-a",
            permissions=self.permissions,
        )
        self.tenant_a = self.tenant
        self.tenant_b = Tenant.objects.create(name="WP-13 API B", slug="wp-13-api-b")
        self.rule_a = AlertRule.objects.create(
            tenant=self.tenant_a,
            name="WP-13 API Rule A",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        self.rule_b = AlertRule._base_manager.create(
            tenant=self.tenant_b,
            name="WP-13 API Rule B",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        content_type = ContentType.objects.get_for_model(AlertRule)
        self.alert_a_delivered = AlertLog._base_manager.create(
            tenant=self.tenant_a,
            rule=self.rule_a,
            subject="A delivered",
            message="m",
            content_type=content_type,
            object_id=1,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_DELIVERED,
            delivery_attempts=1,
            last_delivery_id="run-a",
            last_delivery_error=None,
        )
        self.alert_a_failed = AlertLog._base_manager.create(
            tenant=self.tenant_a,
            rule=self.rule_a,
            subject="A failed",
            message="m",
            content_type=content_type,
            object_id=2,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_FAILED,
            delivery_attempts=1,
            last_delivery_id="run-b",
            last_delivery_error="SMTPException",
        )
        self.alert_b_failed = AlertLog._base_manager.create(
            tenant=self.tenant_b,
            rule=self.rule_b,
            subject="B failed secret",
            message="B secret",
            content_type=content_type,
            object_id=2,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_FAILED,
            last_delivery_error="SMTPException",
        )
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

    def _list(self):
        return reverse("api:extras_api:alertlog-list")

    @staticmethod
    def _rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_api_serializes_delivery_observability_fields(self):
        response = self.client.get(self._list())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = next(r for r in self._rows(response) if r["id"] == self.alert_a_failed.pk)
        self.assertEqual(row["delivery_outcome"], AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(row["delivery_attempts"], 1)
        self.assertEqual(row["last_delivery_id"], "run-b")
        self.assertEqual(row["last_delivery_error"], "SMTPException")
        self.assertIn("delivery_status", row)

    def test_delivery_outcome_filter_is_applied_within_tenant(self):
        response = self.client.get(self._list(), {"delivery_outcome": AlertLog.DELIVERY_OUTCOME_FAILED})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = self._rows(response)
        self.assertEqual({row["id"] for row in rows}, {self.alert_a_failed.pk})
        self.assertNotIn("B failed secret", str(response.data))
        self.assertNotIn(self.alert_a_delivered.pk, {row["id"] for row in rows})

    def test_tenant_b_failure_payload_is_never_visible_to_tenant_a(self):
        response = self.client.get(self._list(), {"delivery_outcome": AlertLog.DELIVERY_OUTCOME_FAILED})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertNotIn(self.alert_b_failed.pk, {row["id"] for row in self._rows(response)})
        self.assertNotIn("B secret", str(response.data))

    def test_no_manual_redelivery_route_exists(self):
        # WP-13 Path B declares manual redelivery ABSENT; nothing may advertise
        # it — there must be no UI or API route to trigger one.
        with self.assertRaises(NoReverseMatch):
            reverse("extras:alertlog_redeliver", kwargs={"pk": self.alert_a_failed.pk})


class AlertLifecycleIndependentOfDeliveryTests(TransactionTestCase):
    """Delivery failure must not gate creation, acknowledgement, or resolution."""

    def test_lifecycle_survives_every_channel_failing(self):
        from unittest.mock import patch

        from assets.models import Manufacturer
        from core.events import DeliveryDisposition, DeliveryResult
        from core.tasks.context import TaskContext
        from inventory.models import Accessory, AccessoryStock
        from organization.models import Location, Site

        tenant = Tenant.objects.create(name="WP-13 Lifecycle Tenant", slug="wp-13-lifecycle-tenant")
        user = User.objects.create_user(username="wp13-lifecycle", password="x")
        rule = AlertRule.objects.create(
            name="WP-13 Lifecycle Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            tenant=tenant,
        )
        channel = NotificationChannel.objects.create(
            name="WP-13 failing channel",
            channel_type=NotificationChannel.TYPE_IN_APP,
            tenant=tenant,
            enabled=True,
            config={"recipient_users": [user.pk]},
        )
        rule.channels.add(channel)
        manufacturer = Manufacturer.objects.create(name="WP-13 Lifecycle Mfr", slug="wp-13-lifecycle-mfr")
        site = Site.objects.create(name="WP-13 Lifecycle Site", slug="wp-13-lifecycle-site", tenant=tenant)
        location = Location.objects.create(
            name="WP-13 Lifecycle Loc", slug="wp-13-lifecycle-loc", tenant=tenant, site=site
        )
        accessory = Accessory.objects.create(
            name="WP-13 Lifecycle Accessory",
            slug="wp-13-lifecycle-accessory",
            manufacturer=manufacturer,
            tenant=tenant,
            min_qty=5,
        )
        AccessoryStock.objects.create(accessory=accessory, location=location, qty=1)

        failure = DeliveryResult("in_app.deliver", DeliveryDisposition.TERMINAL, True, "backend down", "timeout")
        with patch("extras.tasks.alerts.send_notification_to_channel", return_value=failure):
            with TaskContext(tenant_id=tenant.pk):
                with transaction.atomic():
                    fresh = _evaluate_rule(rule, timezone.now().date(), {})

        alert = AlertLog.unscoped.get(rule=rule)
        self.assertEqual(fresh, 1)
        self.assertEqual(alert.status, AlertLog.STATUS_ACTIVE, "creation is not gated by delivery")
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(alert.last_delivery_error, "timeout")

        # Acknowledgement and resolution proceed despite the failed delivery.
        alert.status = AlertLog.STATUS_ACKNOWLEDGED
        alert.acknowledged_by = user
        alert.save(update_fields=["status", "acknowledged_by"])
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertLog.STATUS_ACKNOWLEDGED)
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_FAILED)

        alert.status = AlertLog.STATUS_RESOLVED
        alert.resolved_by = user
        alert.save(update_fields=["status", "resolved_by"])
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertLog.STATUS_RESOLVED)
        # The Stable inbox projection never mutated delivery bookkeeping.
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_FAILED)
        self.assertEqual(alert.delivery_attempts, 1)


class AlertDeliveryTableRenderTests(TestCase):
    def test_render_delivery_badges(self):
        tenant = Tenant.objects.create(name="WP-13 Table Tenant", slug="wp-13-table-tenant")
        rule = AlertRule.objects.create(
            name="WP-13 Table Rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
            tenant=tenant,
        )
        content_type = ContentType.objects.get_for_model(AlertRule)
        table = AlertLogTable([])

        delivered = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="d",
            message="m",
            content_type=content_type,
            object_id=1,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_DELIVERED,
            delivery_status={"7": {"disposition": "success", "operation": "in_app.deliver"}},
        )
        self.assertIn("badge bg-success", table.render_delivery(delivered))

        failed = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="f",
            message="m",
            content_type=content_type,
            object_id=2,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_FAILED,
            delivery_status={
                "7": {
                    "disposition": "terminal",
                    "operation": "email.deliver",
                    "error_class": "SMTPException",
                }
            },
        )
        rendered = table.render_delivery(failed)
        self.assertIn("badge bg-danger", rendered)
        self.assertIn("SMTPException", rendered)

        pending = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="p",
            message="m",
            content_type=content_type,
            object_id=3,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_PENDING,
            delivery_status={"__dispatch__": "pending"},
        )
        self.assertIn("badge bg-info", table.render_delivery(pending))

        no_channels = AlertLog._base_manager.create(
            tenant=tenant,
            rule=rule,
            subject="n",
            message="m",
            content_type=content_type,
            object_id=4,
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_NONE,
            delivery_status={"__no_channels__": "no channels attached to this rule"},
        )
        self.assertIn("badge bg-secondary", table.render_delivery(no_channels))


@isolate_migration_tests
@pytest.mark.serial_only
class AlertDeliveryOutcomeMigrationTests(IsolatedMigrationTestCase):
    """Migration 0108 derives filterable outcomes from legacy payloads (N11/U4)."""

    reset_sequences = True

    migrate_from = ("extras", "0107_scheduledreportscopeauthorization_revocation")
    migrate_to = ("extras", "0108_alertlog_delivery_outcome")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)

    def _migrate(self, target):
        self.executor = MigrationExecutor(connection)
        return self.executor.migrate([target])

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_derives_outcomes_and_reverse_preserves_rows(self):
        old_apps = self._migrate(self.migrate_from).apps
        Tenant = old_apps.get_model("organization", "Tenant")
        AlertRule = old_apps.get_model("extras", "AlertRule")
        AlertLog = old_apps.get_model("extras", "AlertLog")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        tenant = Tenant.objects.create(name="WP-13 Migration Tenant", slug="wp-13-migration-tenant")
        rule = AlertRule.objects.create(
            tenant_id=tenant.pk,
            name="WP-13 Migration Rule",
            alert_type="low_stock",
            threshold_value=1,
        )
        rule_ct = ContentType.objects.get(app_label="extras", model="alertrule")
        cases = [
            ({"7": "ok"}, "delivered"),
            ({"7": "failed"}, "failed"),
            ({"7": "error: SMTP rejected"}, "failed"),
            ({"7": "retryable"}, "failed"),
            ({"__dispatch__": "pending"}, "pending"),
            ({"__dispatch__": "terminal"}, "failed"),
            ({"__no_channels__": "no channels attached to this rule"}, "none"),
            ({}, "none"),
            ({"7": {"disposition": "success", "operation": "in_app.deliver"}}, "delivered"),
            ({"7": {"disposition": "terminal", "error_class": "SMTPException"}}, "failed"),
        ]
        expected = {}
        for index, (payload, outcome) in enumerate(cases):
            log = AlertLog.objects.create(
                rule_id=rule.pk,
                subject=f"case-{index}",
                message="m",
                content_type_id=rule_ct.pk,
                object_id=index + 1,
                tenant_id=tenant.pk,
                status="active",
                delivery_status=payload,
            )
            expected[log.pk] = outcome

        new_apps = self._migrate(self.migrate_to).apps
        NewAlertLog = new_apps.get_model("extras", "AlertLog")
        for pk, outcome in expected.items():
            self.assertEqual(
                NewAlertLog.objects.get(pk=pk).delivery_outcome,
                outcome,
                f"payload case {pk} derived wrong outcome",
            )

        # Reverse: added fields drop, rows and original payloads survive.
        self._migrate(self.migrate_from)
        old_again = MigrationExecutor(connection).loader.project_state(self.migrate_from).apps
        OldAlertLog = old_again.get_model("extras", "AlertLog")
        self.assertEqual(OldAlertLog.objects.count(), len(cases))
        first = OldAlertLog.objects.order_by("pk").first()
        self.assertEqual(first.delivery_status, {"7": "ok"})
