"""Cross-cutting security characterization for the issue #445 ownership move."""

from django.test import SimpleTestCase

from core.events import NOTIFICATION_CHANNEL_TYPES, DeliveryDisposition, DeliveryResult
from extras.models import NotificationChannel


class DeliveryPrimitiveSecurityTests(SimpleTestCase):
    def test_delivery_result_is_typed_and_boolean_only_on_success(self):
        success = DeliveryResult("probe", DeliveryDisposition.SUCCESS)
        terminal = DeliveryResult("probe", DeliveryDisposition.TERMINAL, error_class="safe.code")
        self.assertTrue(success)
        self.assertFalse(terminal)
        self.assertEqual(terminal.error_class, "safe.code")

    def test_channel_vocabulary_matches_the_delivery_boundary(self):
        domain_values = {value for value, _label in NotificationChannel.CHANNEL_TYPE_CHOICES}
        self.assertEqual(domain_values, set(NOTIFICATION_CHANNEL_TYPES))
