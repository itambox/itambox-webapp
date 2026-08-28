"""Regression tests for notification links that lead to file downloads."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOTIFICATION_SHELL_TEMPLATES = (
    PROJECT_ROOT / "templates" / "layout.html",
    PROJECT_ROOT / "templates" / "global_includes" / "_topbar.html",
    PROJECT_ROOT / "templates" / "htmx" / "notification_dropdown.html",
    PROJECT_ROOT / "templates" / "users" / "notifications.html",
)


def test_notification_links_are_not_intercepted_by_htmx_boost():
    """Notification redirects must remain native so attachment downloads work."""
    offenders = []
    for template_path in NOTIFICATION_SHELL_TEMPLATES:
        source = template_path.read_text(encoding="utf-8")
        for match in re.finditer(r"<a\b(?P<attributes>[^>]*)>", source, re.DOTALL):
            attributes = match.group("attributes")
            if "users:view_notification" in attributes and 'hx-boost="false"' not in attributes:
                offenders.append(str(template_path.relative_to(PROJECT_ROOT)))

    assert not offenders, "Notification links must opt out of hx-boost: " + ", ".join(offenders)
