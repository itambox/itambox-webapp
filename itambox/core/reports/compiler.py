"""Common report compilation orchestration."""

from django.utils import timezone

from itambox.middleware import get_current_user

from .columns import headers_for
from .contracts import ReportRequest
from .registry import get_report_provider


def _resolve_report_scope(active_tenant, filter_tenants):
    """Apply the existing cross-tenant permission gate without domain imports."""
    if filter_tenants:
        return filter_tenants

    user = get_current_user()
    if user is not None and user.has_perm("reports.view_cross_tenant_reports"):
        return filter_tenants
    if active_tenant is not None:
        return [active_tenant]
    raise PermissionError(
        "Cross-tenant report aggregation requires the 'reports.view_cross_tenant_reports' permission."
    )


def _group_rows(rows, group_by_field):
    grouped_data = {}
    if group_by_field:
        for row in rows:
            group_key = row.get("_group_by", "General")
            grouped_data.setdefault(group_key, []).append(row)
    else:
        grouped_data["General"] = rows
    return grouped_data


def compile_report_context(template, active_tenant=None, filter_tenants=None):
    """Compile a report through its registered domain provider.

    The return shape intentionally remains the historical six-tuple because
    preview, download, scheduled reporting, and external integrations unpack
    it directly.  Domain providers own querying and row/summary/chart logic;
    this function owns scope authorization and common context assembly only.
    """
    filter_tenants = _resolve_report_scope(active_tenant, filter_tenants)
    provider = get_report_provider(template.report_type)
    columns = tuple(provider.build_columns(template))
    request = ReportRequest(
        template=template,
        active_tenant=active_tenant,
        filter_tenants=tuple(filter_tenants or ()),
        columns=columns,
        user=get_current_user(),
        as_of=timezone.now(),
    )
    result = provider.build(request)
    headers = result.headers if result.headers is not None else headers_for(columns)
    rows = result.rows
    summary_cards = result.summary_cards
    grouped_data = _group_rows(rows, template.group_by_field)
    context_data = {
        "report_name": template.name,
        "description": template.description,
        "generated_at": request.as_of,
        "headers": headers,
        "grouped_data": grouped_data,
        "summary_cards": summary_cards,
        "distribution_chart": result.chart_svg,
        "style_preset": template.style_preset,
        "is_compact": template.style_preset == "compact",
        "is_financial": template.style_preset == "financial",
    }
    return headers, rows, summary_cards, grouped_data, result.chart_svg, context_data
