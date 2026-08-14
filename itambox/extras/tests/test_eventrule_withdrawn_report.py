import io

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TransactionTestCase

from extras.models import EventRule, Tag
from organization.models import Tenant


@pytest.mark.serial_only
class EventRuleWithdrawnReportTests(TransactionTestCase):
    def _run_report(self):
        stdout = io.StringIO()
        call_command("eventrule_withdrawn_report", stdout=stdout)
        return stdout.getvalue()

    def _create_rule(self, **kwargs):
        defaults = {
            "model": ContentType.objects.get_for_model(Tag),
            "events": ["create"],
            "action_type": EventRule.ACTION_NOTIFICATION,
            "enabled": True,
        }
        defaults.update(kwargs)
        return EventRule.objects.create(**defaults)

    def test_report_lists_only_withdrawn_rules_without_json_values(self):
        tenant = Tenant.objects.create(name="Report Tenant", slug="report-tenant")
        system_rule = self._create_rule(
            name="Alpha system rule",
            conditions={"field": "name", "op": "eq", "value": "SYSTEM_CONDITION_SECRET"},
            action_config={"secret": "SYSTEM_ACTION_CONFIG_SECRET"},
            tenant=None,
        )
        tenant_rule = self._create_rule(
            name="Beta tenant rule",
            conditions={"rules": [{"field": "name", "op": "eq", "value": "TENANT_CONDITION_SECRET"}]},
            action_config={"token": "TENANT_ACTION_CONFIG_SECRET"},
            enabled=False,
            tenant=tenant,
        )
        self._create_rule(name="Gamma ordinary rule", conditions={}, tenant=tenant)

        output = self._run_report()
        repeated_output = self._run_report()

        self.assertEqual(output, repeated_output)
        self.assertIn(f"pk={system_rule.pk} name=Alpha system rule tenant=(system-wide)", output)
        self.assertIn(f"pk={tenant_rule.pk} name=Beta tenant rule tenant=report-tenant", output)
        self.assertIn(f"action_type=notification enabled=True updated_at={system_rule.updated_at.isoformat()}", output)
        self.assertIn(f"action_type=notification enabled=False updated_at={tenant_rule.updated_at.isoformat()}", output)
        self.assertNotIn("Gamma ordinary rule", output)
        self.assertLess(output.index("Alpha system rule"), output.index("Beta tenant rule"))
        for secret in (
            "SYSTEM_CONDITION_SECRET",
            "TENANT_CONDITION_SECRET",
            "SYSTEM_ACTION_CONFIG_SECRET",
            "TENANT_ACTION_CONFIG_SECRET",
        ):
            self.assertNotIn(secret, output)
        self.assertIn("action_type=notification", output)
        self.assertIn("enabled=False", output)
        self.assertIn("2 rule(s) with withdrawn conditions — these rules will not dispatch in 1.0.", output)

    def test_report_returns_zero_for_no_withdrawn_rules(self):
        self._create_rule(name="Empty conditions rule", conditions={})

        self.assertEqual(
            self._run_report(),
            "0 rule(s) with withdrawn conditions — these rules will not dispatch in 1.0.\n",
        )
