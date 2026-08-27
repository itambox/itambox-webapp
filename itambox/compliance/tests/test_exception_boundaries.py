from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from compliance.views import _safe_dispatch_custody


class CustodyEventBoundaryTests(SimpleTestCase):
    @patch("extras.services.events.dispatch_event", side_effect=RuntimeError("event backend offline"))
    def test_dispatch_failure_is_logged_with_non_secret_context(self, dispatch):
        receipt = SimpleNamespace(pk=31)

        with self.assertLogs("compliance.views", level="ERROR") as logs:
            _safe_dispatch_custody(receipt, actor_id=41, tenant_id=59)

        self.assertEqual(dispatch.call_count, 1)
        output = "\n".join(logs.output)
        self.assertIn("receipt_id=31", output)
        self.assertIn("tenant_id=59", output)
        self.assertIn("actor_id=41", output)
