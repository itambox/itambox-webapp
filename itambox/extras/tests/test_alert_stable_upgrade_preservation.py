from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TransactionTestCase

from assets.models import Manufacturer
from core.events import DeliveryDisposition, DeliveryResult
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from extras.models import AlertLog, AlertRule, NotificationChannel
from extras.tasks.alerts import _delivery_outcome, evaluate_alert_rules_task
from inventory.models import Accessory, AccessoryStock
from organization.models import Location, Site, Tenant


class AlertStableUpgradePreservationTests(TenantTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant._base_manager.create(name="Alert upgrade tenant", slug="alert-upgrade-tenant")
        self.other_tenant = Tenant._base_manager.create(name="Disabled-rule tenant", slug="disabled-rule-tenant")
        self.manufacturer = Manufacturer.objects.create(name="Upgrade manufacturer", slug="upgrade-manufacturer")
        self.site = Site.objects.create(name="Upgrade site", slug="upgrade-site", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Upgrade location", slug="upgrade-location", site=self.site, tenant=self.tenant
        )
        self.other_site = Site.objects.create(name="Disabled site", slug="disabled-rule-site", tenant=self.other_tenant)
        self.other_location = Location.objects.create(
            name="Disabled location",
            slug="disabled-rule-location",
            site=self.other_site,
            tenant=self.other_tenant,
        )
        self.enabled_channel = NotificationChannel._base_manager.create(
            name="Upgrade enabled channel",
            channel_type=NotificationChannel.TYPE_IN_APP,
            enabled=True,
            tenant=self.tenant,
        )
        self.disabled_channel = NotificationChannel._base_manager.create(
            name="Upgrade disabled channel",
            channel_type=NotificationChannel.TYPE_IN_APP,
            enabled=False,
            tenant=self.tenant,
        )
        self.active_rule = AlertRule._base_manager.create(
            name="Upgrade active rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            renotify_interval_days=0,
            is_active=True,
            tenant=self.tenant,
        )
        self.disabled_rule = AlertRule._base_manager.create(
            name="Upgrade disabled rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            is_active=False,
            tenant=self.other_tenant,
        )
        with TaskContext(tenant_id=self.tenant.pk, user_id=None):
            self.active_rule.channels.add(self.enabled_channel, self.disabled_channel)

        with self.tenant_context(self.tenant):
            self.legacy_accessory, self.legacy_stock = self._make_low_stock_accessory(
                "Legacy alert accessory", "legacy-alert-accessory", self.location
            )
            self.pending_accessory, self.pending_stock = self._make_low_stock_accessory(
                "Pending alert accessory", "pending-alert-accessory", self.location
            )
            self.terminal_accessory, self.terminal_stock = self._make_low_stock_accessory(
                "Terminal alert accessory", "terminal-alert-accessory", self.location
            )
        with self.tenant_context(self.other_tenant):
            self.disabled_accessory, self.disabled_stock = self._make_low_stock_accessory(
                "Disabled-rule accessory", "disabled-rule-accessory", self.other_location
            )

        self.accessory_ct = ContentType.objects.get_for_model(Accessory)
        self.legacy = AlertLog._base_manager.create(
            tenant=self.tenant,
            rule=self.active_rule,
            subject="Legacy delivered alert",
            message="Legacy delivery history",
            content_type=self.accessory_ct,
            object_id=self.legacy_accessory.pk,
            delivery_status={str(self.enabled_channel.pk): "ok"},
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_DELIVERED,
            delivery_attempts=1,
            last_delivery_id="legacy-delivery-id",
        )
        self.pending = AlertLog._base_manager.create(
            tenant=self.tenant,
            rule=self.active_rule,
            subject="Pending alert",
            message="Dispatch interrupted before completion",
            content_type=self.accessory_ct,
            object_id=self.pending_accessory.pk,
            delivery_status={"__dispatch__": "pending"},
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_PENDING,
            delivery_attempts=1,
            last_delivery_id="old-pending-delivery-id",
        )
        self.terminal = AlertLog._base_manager.create(
            tenant=self.tenant,
            rule=self.active_rule,
            subject="Terminal failed alert",
            message="Terminal delivery history",
            content_type=self.accessory_ct,
            object_id=self.terminal_accessory.pk,
            delivery_status={
                str(self.enabled_channel.pk): {
                    "disposition": "terminal",
                    "error_class": "SMTPException",
                }
            },
            delivery_outcome=AlertLog.DELIVERY_OUTCOME_FAILED,
            delivery_attempts=2,
            last_delivery_id="terminal-delivery-id",
        )

    def _make_low_stock_accessory(self, name, slug, location):
        accessory = Accessory.objects.create(
            name=name,
            slug=slug,
            manufacturer=self.manufacturer,
            tenant=location.tenant,
            min_qty=5,
        )
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=1)
        return accessory, stock

    @staticmethod
    def _snapshot(row):
        return {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}

    @staticmethod
    def _success_result():
        return DeliveryResult("in_app.deliver", DeliveryDisposition.SUCCESS)

    def _suppress_pending_retry(self):
        AlertLog._base_manager.filter(pk=self.pending.pk).update(status=AlertLog.STATUS_RESOLVED)
        AccessoryStock._base_manager.filter(pk=self.pending_stock.pk).update(qty=100)

    def test_promotion_preserves_history_rows(self):
        rows = (self.legacy, self.terminal)
        before = {row.pk: self._snapshot(row) for row in rows}

        with patch("extras.tasks.alerts.send_notification_to_channel", return_value=self._success_result()) as sender:
            evaluate_alert_rules_task()

        sender.assert_called_once()
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(self._snapshot(row), before[row.pk])

    def test_pending_dispatch_is_recovered_on_the_next_evaluation(self):
        previous_delivery_id = self.pending.last_delivery_id
        previous_attempts = self.pending.delivery_attempts

        with (
            patch("extras.tasks.alerts.uuid4", return_value="recovered-delivery-id"),
            patch("extras.tasks.alerts.send_notification_to_channel", return_value=self._success_result()) as sender,
        ):
            evaluate_alert_rules_task()

        sender.assert_called_once()
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.delivery_attempts, previous_attempts + 1)
        self.assertNotEqual(self.pending.last_delivery_id, previous_delivery_id)
        self.assertEqual(self.pending.last_delivery_id, "recovered-delivery-id")
        self.assertEqual(self.pending.delivery_outcome, AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertEqual(self.pending.delivery_status[str(self.enabled_channel.pk)]["disposition"], "success")

    def test_terminal_failure_is_not_retried(self):
        self._suppress_pending_retry()
        before = self._snapshot(self.terminal)

        with patch("extras.tasks.alerts.send_notification_to_channel") as sender:
            evaluate_alert_rules_task()

        sender.assert_not_called()
        self.terminal.refresh_from_db()
        self.assertEqual(self._snapshot(self.terminal), before)
        self.assertEqual(self.terminal.delivery_attempts, before["delivery_attempts"])

    def test_disabled_rule_and_disabled_channel_stay_disabled(self):
        self._suppress_pending_retry()
        with self.tenant_context(self.tenant):
            fresh_accessory, _stock = self._make_low_stock_accessory(
                "Fresh enabled-gate accessory", "fresh-enabled-gate-accessory", self.location
            )

        with patch("extras.tasks.alerts.send_notification_to_channel", return_value=self._success_result()) as sender:
            evaluate_alert_rules_task()

        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[0].pk, self.enabled_channel.pk)
        self.assertFalse(NotificationChannel._base_manager.get(pk=self.disabled_channel.pk).enabled)
        self.assertFalse(AlertRule._base_manager.get(pk=self.disabled_rule.pk).is_active)
        self.assertFalse(AlertLog._base_manager.filter(rule=self.disabled_rule).exists())

        fresh_log = AlertLog._base_manager.get(rule=self.active_rule, object_id=fresh_accessory.pk)
        self.assertIn(str(self.enabled_channel.pk), fresh_log.delivery_status)
        self.assertNotIn(str(self.disabled_channel.pk), fresh_log.delivery_status)

    def test_upgrade_keeps_legacy_outcome_derivation(self):
        self.assertEqual(_delivery_outcome(self.legacy.delivery_status), AlertLog.DELIVERY_OUTCOME_DELIVERED)
        self.assertEqual(_delivery_outcome(self.terminal.delivery_status), AlertLog.DELIVERY_OUTCOME_FAILED)
