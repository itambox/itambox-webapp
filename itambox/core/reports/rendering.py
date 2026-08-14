"""Shared report rendering policy for workers and HTTP exports."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.template import Context, Template
from django.utils.translation import gettext as _

from core.csv_utils import csv_safe
from core.features import report_designer_probe
from core.reports.templates import get_polished_system_html_template


def custom_html_execution_allowed(template):
    """Return whether custom HTML may execute for this persisted template."""
    return bool(getattr(template, "legacy_designer_grandfathered", False) or report_designer_probe().active)


_CUSTOM_CONTEXT_KEYS = frozenset(
    {
        "report_name",
        "description",
        "generated_at",
        "headers",
        "grouped_data",
        "summary_cards",
        "distribution_chart",
        "style_preset",
        "is_compact",
        "is_financial",
        "request",
    }
)


def _safe_custom_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_custom_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_custom_value(item) for item in value]
    return None


def _custom_context(context_data):
    return {key: _safe_custom_value(context_data[key]) for key in _CUSTOM_CONTEXT_KEYS if key in context_data}


def render_report_html(context_data, template=None):
    """Render custom sandboxed HTML when allowed, otherwise curated HTML."""
    template_content = (getattr(template, "template_content", "") or "").strip()
    if template_content and custom_html_execution_allowed(template):
        # inline import: optional-dependency: Jinja2 is only needed for custom HTML execution.
        from jinja2.sandbox import SandboxedEnvironment

        return SandboxedEnvironment(autoescape=True).from_string(template_content).render(_custom_context(context_data))
    return Template(get_polished_system_html_template()).render(Context(context_data))


def _card_value(summary_cards, label):
    for card in summary_cards or []:
        if card.get("label") == label:
            return card.get("value", "")
    return ""


def render_report_csv(template, headers, rows, summary_cards=None, grouped_data=None):
    """Render the stable visual CSV or the historical legacy CSV shape."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if not getattr(template, "advanced_mode", False):
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_safe(row.get(header, "-")) for header in headers])
        return buffer.getvalue()

    total_rows = len(rows)
    total_active = total_rows
    acquisition_display = _card_value(summary_cards, _("Total Acquisition Sum"))
    monthly_spend_display = _card_value(summary_cards, _("Est. Monthly Spend"))
    grouped_data = grouped_data or {}

    if template.report_type == "asset_summary":
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Total Hardware Assets", total_rows])
        writer.writerow(["Total Acquisition Sum", acquisition_display])
        writer.writerow([])
        writer.writerow(["Location", "Allocated Count"])
        for group, group_rows in grouped_data.items():
            writer.writerow([csv_safe(group), len(group_rows)])
    elif template.report_type == "license_utilization":
        writer.writerow(["License", "Software", "Total Seats", "Assigned Seats", "Available Seats", "Utilization Rate"])
        for row in rows:
            writer.writerow(
                [
                    csv_safe(row.get(_("License Name"))),
                    csv_safe(row.get(_("Software"))),
                    row.get(_("Total Seats")),
                    row.get(_("Assigned Seats")),
                    row.get(_("Available Seats")),
                    row.get(_("Utilization Rate")),
                ]
            )
    elif template.report_type == "subscription_renewals":
        writer.writerow(["Active Subscriptions", total_active])
        writer.writerow(["Est. Monthly Spend", monthly_spend_display])
        writer.writerow([])
        writer.writerow(["Subscription", "Provider", "Billing Cycle", "Cost", "End Date"])
        for row in rows:
            writer.writerow(
                [
                    csv_safe(row.get(_("Subscription Name"))),
                    csv_safe(row.get(_("Provider"))),
                    csv_safe(row.get(_("Billing Cycle"))),
                    row.get(_("Cost")),
                    row.get(_("End Date")),
                ]
            )
    else:
        # Legacy mode was never defined for newer providers; keep their normal
        # canonical-column CSV rather than inventing a new shape.
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_safe(row.get(header, "-")) for header in headers])
    return buffer.getvalue()
