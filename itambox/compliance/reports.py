"""Compliance-domain report providers."""

from django.utils.translation import gettext as _

from compliance.models import CustodyReceipt
from core.reports.charts import generate_doughnut_chart
from core.reports.contracts import ReportDefinition, ReportRequest
from core.reports.registry import register_report_provider


class CustodyComplianceReportProvider(ReportDefinition):
    """Custody & EULA sign-off: which receipts are signed, by whom, against what."""

    report_type = "custody_compliance"
    permission = "compliance.view_custodyreceipt"
    #: A receipt has no tenant of its own; it belongs to the asset it covers.
    tenant_field = "asset__tenant"
    default_columns = (
        "custody_asset",
        "custody_holder",
        "custody_status",
        "custody_accepted_date",
        "custody_eula_version",
        "custody_signature_provider",
    )

    cells = {
        "custody_asset": lambda receipt, request: str(receipt.asset) if receipt.asset else "-",
        "custody_holder": lambda receipt, request: str(receipt.holder) if receipt.holder else "-",
        "custody_status": lambda receipt, request: receipt.get_acceptance_status_display(),
        "custody_accepted_date": lambda receipt, request: (
            receipt.accepted_date.strftime("%Y-%m-%d %H:%M") if receipt.accepted_date else "-"
        ),
        "custody_eula_version": lambda receipt, request: receipt.eula_version or "-",
        "custody_signature_provider": lambda receipt, request: receipt.signature_provider or "-",
        "custody_qms_reference": lambda receipt, request: receipt.qms_reference or "-",
        "custody_ip_address": lambda receipt, request: str(receipt.ip_address) if receipt.ip_address else "-",
        "custody_created_date": lambda receipt, request: (
            receipt.created_date.strftime("%Y-%m-%d") if receipt.created_date else "-"
        ),
    }

    sample_cells = {
        "custody_asset": "AST-MOCK-001: MacBook Pro (Mock)",
        "custody_holder": "Alex Dev (Mock)",
        "custody_status": "Accepted",
        "custody_accepted_date": "2026-06-01 09:00",
        "custody_eula_version": "1.0",
        "custody_signature_provider": "local",
        "custody_qms_reference": "QMS-2026-001",
        "custody_ip_address": "192.168.1.10",
        "custody_created_date": "2026-06-01",
    }

    group_resolvers = {
        "custody_status": lambda receipt, request: receipt.get_acceptance_status_display(),
        "custody_signature_provider": lambda receipt, request: receipt.signature_provider or _("Unknown"),
        "custody_eula_version": lambda receipt, request: receipt.eula_version or _("Unknown"),
    }

    sample_group_keys = {
        "custody_status": "Accepted",
        "custody_signature_provider": "local",
        "custody_eula_version": "1.0",
    }

    def get_queryset(self, request: ReportRequest):
        # CustodyReceipt is not soft-deletable, so there is no deleted_at to
        # filter, and its default manager is deliberately unscoped — the public
        # token sign flow resolves a receipt without a tenant context — so the
        # report's own tenant scoping is the only boundary here.
        queryset = CustodyReceipt.objects.select_related("asset", "holder")
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(receipt, request) for receipt in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        total_receipts = queryset.count()
        pending_count = queryset.filter(acceptance_status=CustodyReceipt.STATUS_PENDING).count()
        accepted_count = queryset.filter(acceptance_status=CustodyReceipt.STATUS_ACCEPTED).count()
        acceptance_rate = round((accepted_count / total_receipts * 100), 1) if total_receipts > 0 else 0.0
        return [
            {"label": _("Total Receipts"), "value": str(total_receipts)},
            {"label": _("Pending Sign-offs"), "value": str(pending_count)},
            {"label": _("Acceptance Rate"), "value": f"{acceptance_rate}%"},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        status_counts = {}
        for receipt in records:
            label = receipt.get_acceptance_status_display()
            status_counts[label] = status_counts.get(label, 0) + 1
        chart_data = [{"label": label, "value": count} for label, count in status_counts.items()]
        return generate_doughnut_chart(chart_data, title=_("Receipt Acceptance Status Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Receipts"), "value": "1 (Mock)"},
            {"label": _("Pending Sign-offs"), "value": "0 (Mock)"},
            {"label": _("Acceptance Rate"), "value": "100.0% (Mock)"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart(
            [{"label": _("Accepted"), "value": 1}], title=_("Receipt Acceptance Status Distribution")
        )


register_report_provider(CustodyComplianceReportProvider())
