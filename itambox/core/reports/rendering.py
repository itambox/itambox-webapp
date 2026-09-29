"""Shared report rendering policy for workers and HTTP exports."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.template import Context, Template
from django.utils.html import escape
from django.utils.translation import gettext as _

from core.csv_utils import csv_safe
from core.reports.templates import get_polished_system_html_template


def report_disclosure_text(result, row_limit, truncated_template=None):
    """One sentence describing a capped or sample-only output, or ``""``.

    Truncation always carries its determinate total: the compiler counts the
    scope whenever the row window is full, so the sentence can state both
    numbers.  The same sentence reaches every output surface (HTML/PDF banner,
    CSV/XLSX trailer, scheduled mail body) so no format quietly implies the
    window is the full population.  ``truncated_template`` lets a provider
    whose window is not a single first-N slice (hardware inventory) state its
    real shape.
    """
    if getattr(result, "is_sample", False):
        return _("No records matched this scope; this output shows the report's sample data.")
    if getattr(result, "truncated", False):
        if truncated_template:
            return _(truncated_template) % {
                "limit": row_limit,
                "total": getattr(result, "total_rows", None),
            }
        return _("Showing the first %(limit)s of %(total)s matching rows.") % {
            "limit": row_limit,
            "total": getattr(result, "total_rows", None),
        }
    return ""


def report_disclosure_notice(context_data):
    """The escaped disclosure notice for a capped or sample-only output, or ``""``.

    Class-only markup by policy: inline ``style`` attributes are banned in
    Python emitters (CSP gate). The class is styled in the application
    stylesheet and in the polished standalone report template.
    """
    text = str(context_data.get("disclosure_text") or "").strip()
    if not text:
        return ""
    return f'<aside class="report-output-disclosure" data-report-disclosure="true">{escape(text)}</aside>'


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
        "is_sample",
        "row_limit",
        "truncated",
        "total_rows",
        "disclosure_text",
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
    """Render custom sandboxed HTML when present, otherwise the curated HTML.

    Custom HTML executes through the sandboxed environment whenever template
    content is stored.  A capped window or sample-only output appends the
    disclosure notice to the custom output as well: forwarding the artifact
    must not strip the qualifier.
    """
    template_content = (getattr(template, "template_content", "") or "").strip()
    if template_content:
        # inline import: optional-dependency: Jinja2 is only needed for custom HTML execution.
        from jinja2.sandbox import SandboxedEnvironment

        rendered = (
            SandboxedEnvironment(autoescape=True).from_string(template_content).render(_custom_context(context_data))
        )
        notice = report_disclosure_notice(context_data)
        return f"{rendered}\n{notice}" if notice else rendered
    return Template(get_polished_system_html_template()).render(Context(context_data))


def _append_csv_disclosure(writer, disclosure_text):
    """Append the truncation/sample disclosure as a final, separated row."""
    if not disclosure_text:
        return
    writer.writerow([])
    writer.writerow([csv_safe(disclosure_text)])


def _card_value(summary_cards, label):
    for card in summary_cards or []:
        if card.get("label") == label:
            return card.get("value", "")
    return ""


def _numeric_card_total(summary_cards, label, fallback):
    """A summary card's count when it is a plain number, else ``fallback``.

    The legacy CSV shape labels its metric lines with whole-scope numbers; the
    summary cards carry them. Sample cards read like ``1 (Mock)`` and keep the
    rendered-window fallback, so only determinate counts switch the number.
    """
    value = _card_value(summary_cards, label)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def render_report_csv(template, headers, rows, summary_cards=None, grouped_data=None, disclosure_text=""):
    """Render the stable visual CSV or the historical legacy CSV shape.

    ``disclosure_text`` is appended as a clearly separated trailer row when
    the compiled window was capped or the report shows its sample, so the
    file itself states that it is not the full population.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if not getattr(template, "advanced_mode", False):
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_safe(row.get(header, "-")) for header in headers])
        _append_csv_disclosure(writer, disclosure_text)
        return buffer.getvalue()

    total_rows = len(rows)
    total_active = _numeric_card_total(summary_cards, _("Active Subscriptions"), total_rows)
    acquisition_display = _card_value(summary_cards, _("Total Acquisition Sum"))
    monthly_spend_display = _card_value(summary_cards, _("Est. Monthly Spend"))
    grouped_data = grouped_data or {}

    if template.report_type == "asset_summary":
        writer.writerow(["Metric", "Value"])
        writer.writerow(
            ["Total Hardware Assets", _numeric_card_total(summary_cards, _("Total Hardware Assets"), total_rows)]
        )
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
        writer.writerow(["Subscription", "Supplier", "Billing Cycle", "Cost", "End Date"])
        for row in rows:
            writer.writerow(
                [
                    csv_safe(row.get(_("Subscription Name"))),
                    csv_safe(row.get(_("Supplier"))),
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
    _append_csv_disclosure(writer, disclosure_text)
    return buffer.getvalue()
