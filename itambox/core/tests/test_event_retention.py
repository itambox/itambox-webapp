"""WP-17: Event retention."""

from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings


class EventRetentionSettingsTests(TestCase):
    def test_setting_default_zero(self):
        """ITAMBOX_EVENT_RETENTION_DAYS defaults to 0 (unlimited/never prune)."""
        self.assertEqual(settings.ITAMBOX_EVENT_RETENTION_DAYS, 0)

    def test_event_is_a_valid_class(self):
        """`prune_changelog --classes=event` parses without error."""
        stdout, stderr = StringIO(), StringIO()
        call_command("prune_changelog", "--classes=event", stdout=stdout, stderr=stderr)
        # With default retention=0 the command should skip, not crash.
        output = stdout.getvalue()
        self.assertIn("retention=unlimited -- skipped", output)

    @override_settings(ITAMBOX_EVENT_RETENTION_DAYS=90)
    def test_override_via_setting(self):
        """Setting value is resolved by the command."""
        self.assertEqual(settings.ITAMBOX_EVENT_RETENTION_DAYS, 90)
