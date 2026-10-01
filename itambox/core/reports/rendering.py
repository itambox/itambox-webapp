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


def render_report_csv(headers, rows, disclosure_text=""):
    """Render the canonical columns and append any sample/truncation disclosure.

    ``disclosure_text`` is appended as a clearly separated trailer row when
    the compiled window was capped or the report shows its sample, so the
    file itself states that it is not the full population.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([csv_safe(row.get(header, "-")) for header in headers])
    _append_csv_disclosure(writer, disclosure_text)
    return buffer.getvalue()
