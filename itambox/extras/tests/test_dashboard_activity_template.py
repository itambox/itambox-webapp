from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class ChangelogActivityTemplateTests(SimpleTestCase):
    def test_rendered_change_log_cells_have_mobile_labels(self):
        change = SimpleNamespace(
            time=datetime(2026, 8, 16, 12, 34),
            user_name="qonTrixz",
            user=None,
            action="update",
            changed_object_type=SimpleNamespace(name="asset"),
            changed_object=None,
            object_repr="Asset 1",
            request_id=uuid4(),
        )

        rendered = render_to_string(
            "extras/dashboard/widgets/activity.html",
            {"recent_changes": [change]},
        )

        for label in ("Time", "User", "Full Name", "Action", "Type", "Object", "Request ID"):
            self.assertIn(f'data-label="{label}"', rendered)
