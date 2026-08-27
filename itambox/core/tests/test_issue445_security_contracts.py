"""Cross-cutting security characterization for the issue #445 ownership move."""

import ast
import inspect

from django.test import SimpleTestCase

from core.events import DeliveryDisposition, DeliveryResult, send_notification_to_channel
from extras.models import NotificationChannel


class Issue445DeliveryPrimitiveSecurityTests(SimpleTestCase):
    """PASS for typed results/channel vocabulary; RED for the domain-blind cutover."""

    def test_delivery_result_is_typed_and_boolean_only_on_success(self):
        success = DeliveryResult("probe", DeliveryDisposition.SUCCESS)
        terminal = DeliveryResult("probe", DeliveryDisposition.TERMINAL, error_class="safe.code")
        self.assertTrue(success)
        self.assertFalse(terminal)
        self.assertEqual(terminal.error_class, "safe.code")

    def test_structural_channel_vocabulary_matches_domain_choices(self):
        source = inspect.getsource(send_notification_to_channel)
        domain_values = {value for value, _label in NotificationChannel.CHANNEL_TYPE_CHOICES}
        self.assertEqual(domain_values, {"email", "in_app", "slack", "teams"})
        names = {
            "email": "TYPE_EMAIL",
            "in_app": "TYPE_IN_APP",
            "slack": "TYPE_SLACK",
            "teams": "TYPE_TEAMS",
        }
        for value, constant in names.items():
            self.assertTrue(
                constant in source or repr(value) in source,
                f"missing issue445 structural channel parity contract for {value}",
            )

    def test_core_events_has_no_extras_domain_import(self):
        import core.events as events

        tree = ast.parse(inspect.getsource(events))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        self.assertFalse(
            any(name == "extras" or name.startswith("extras.") for name in imports),
            "missing issue445 domain-blind delivery primitive contract: core.events imports extras",
        )
