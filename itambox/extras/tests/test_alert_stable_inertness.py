from unittest.mock import patch

from django.test import TransactionTestCase

from assets.models import Manufacturer
from core.features import STABLE
from extras.models import AlertLog, AlertRule, NotificationChannel
from extras.tasks.alerts import evaluate_alert_rules_task, run_alert_rule_now
from inventory.models import Accessory, AccessoryStock
from itambox.capabilities import ALWAYS_ON, SOURCE_ALWAYS, ActivationState, registry
from organization.models import Location, Site, Tenant


class AlertStableInertnessTests(TransactionTestCase):
    def _make_low_stock_accessory(self, name, slug):
        tenant = Tenant.objects.create(name=f"{name} tenant", slug=f"{slug}-tenant")
        manufacturer = Manufacturer.objects.create(name=f"{name} manufacturer", slug=f"{slug}-manufacturer")
        site = Site.objects.create(name=f"{name} site", slug=f"{slug}-site", tenant=tenant)
        location = Location.objects.create(name=f"{name} location", slug=f"{slug}-location", site=site, tenant=tenant)
        accessory = Accessory.objects.create(
            name=name,
            slug=slug,
            manufacturer=manufacturer,
            min_qty=5,
        )
        AccessoryStock.objects.create(accessory=accessory, location=location, qty=1)
        return accessory

    def test_stable_alert_capability_is_active_without_configuration_rows(self):
        self.assertEqual(AlertRule._base_manager.count(), 0)
        self.assertEqual(NotificationChannel._base_manager.count(), 0)

        capability = registry.get("alerting.rules")
        self.assertEqual(
            (capability.maturity, capability.activation, capability.activation_source),
            (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
        )
        self.assertIsNone(capability.activation_probe)
        self.assertEqual(capability.limitations, ())
        self.assertEqual(registry.state("alerting.rules"), ActivationState(active=True, value_present=True))

    @patch("extras.tasks.alerts.send_notification_to_channel")
    def test_evaluation_without_rules_creates_no_alerts_and_no_dispatch(self, sender):
        evaluate_alert_rules_task()

        self.assertEqual(AlertLog._base_manager.count(), 0)
        sender.assert_not_called()

    @patch("extras.tasks.alerts.send_notification_to_channel")
    def test_enabled_channel_defaults_alone_do_not_notify(self, sender):
        channel = NotificationChannel.objects.create(
            name="Unattached enabled channel",
            channel_type=NotificationChannel.TYPE_IN_APP,
            enabled=True,
        )
        self.assertTrue(channel.enabled)

        evaluate_alert_rules_task()

        self.assertEqual(AlertLog._base_manager.count(), 0)
        self.assertEqual(AlertRule._base_manager.count(), 0)
        sender.assert_not_called()

    @patch("extras.tasks.alerts.send_notification_to_channel")
    def test_active_rule_without_channels_records_none_outcome(self, sender):
        self._make_low_stock_accessory("Unrouted low stock", "unrouted-low-stock")
        rule = AlertRule.objects.create(
            name="Low stock without channels",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            is_active=True,
        )

        run_alert_rule_now(rule.pk)

        alert = AlertLog._base_manager.get(rule=rule)
        self.assertEqual(AlertLog._base_manager.filter(rule=rule).count(), 1)
        self.assertEqual(alert.delivery_status["__no_channels__"], "no channels attached to this rule")
        self.assertEqual(alert.delivery_outcome, AlertLog.DELIVERY_OUTCOME_NONE)
        sender.assert_not_called()

    @patch("extras.tasks.alerts.send_notification_to_channel")
    def test_disabled_rule_is_never_evaluated_or_enabled(self, sender):
        self._make_low_stock_accessory("Disabled-rule low stock", "disabled-rule-low-stock")
        rule = AlertRule.objects.create(
            name="Disabled low stock rule",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
            is_active=False,
        )

        evaluate_alert_rules_task()

        self.assertFalse(AlertRule._base_manager.get(pk=rule.pk).is_active)
        self.assertFalse(AlertLog._base_manager.filter(rule=rule).exists())
        sender.assert_not_called()
