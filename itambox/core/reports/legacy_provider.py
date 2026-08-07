"""Compatibility provider for report types not extracted yet.

This is intentionally temporary.  It keeps the old branch implementation
behind the provider contract while each domain is moved independently.
"""

from .contracts import PUBLIC_REPORT_TYPES, ReportDefinition, ReportResult
from .legacy import compile_legacy_report_context


class LegacyReportProvider(ReportDefinition):
    report_types = tuple(report_type for report_type in PUBLIC_REPORT_TYPES if report_type != "asset_summary")

    def build(self, request):
        headers, rows, summary_cards, _grouped_data, chart_svg, _context_data = compile_legacy_report_context(
            request.template,
            active_tenant=request.active_tenant,
            filter_tenants=list(request.filter_tenants),
        )
        return ReportResult(
            headers=headers,
            rows=rows,
            summary_cards=summary_cards,
            chart_svg=chart_svg,
            is_sample=any("Mock" in str(value) for row in rows for value in row.values()),
        )
