"""License-domain report providers."""

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from core.reports.charts import generate_doughnut_chart
from core.reports.contracts import ReportDefinition, ReportRequest
from core.reports.registry import register_report_provider
from core.reports.rows import DEFAULT_GROUP
from licenses.models import License


def _utilization_rate(license_record):
    seats = license_record.seats
    if seats > 0:
        return round((license_record.assigned_seats_count / seats * 100), 2)
    return 0


class LicenseUtilizationReportProvider(ReportDefinition):
    """Seat utilization: assigned against purchased seats per license product."""

    report_type = "license_utilization"
    permission = "licenses.view_license"
    default_columns = (
        "license_name",
        "software",
        "seats",
        "assigned_seats",
        "available_seats",
        "utilization_rate",
    )

    cells = {
        "license_name": lambda record, request: record.name or "-",
        "software": lambda record, request: record.software.name if record.software else "-",
        "seats": lambda record, request: str(record.seats),
        "assigned_seats": lambda record, request: str(record.assigned_seats_count),
        "available_seats": lambda record, request: str(record.seats - record.assigned_seats_count),
        "utilization_rate": lambda record, request: f"{_utilization_rate(record)}%",
    }

    sample_cells = {
        "license_name": "Adobe Creative Cloud",
        "software": "Adobe Suite",
        "seats": "50",
        "assigned_seats": "42",
        "available_seats": "8",
        "utilization_rate": "84.0%",
    }

    group_resolvers = {
        "software": lambda record, request: record.software.name if record.software else DEFAULT_GROUP,
    }

    sample_group_keys = {"software": "Adobe Suite"}

    def get_queryset(self, request: ReportRequest):
        # Count only *active* seat assignments. A bare Count('assignments') also
        # tallies soft-deleted (checked-in) seats, overstating utilization and
        # the downstream SAM/financial figures.
        queryset = (
            License.objects.filter(deleted_at__isnull=True)
            .select_related("software")
            .annotate(assigned_seats_count=Count("assignments", filter=Q(assignments__deleted_at__isnull=True)))
        )
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(record, request) for record in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [{"label": _("Total License Products"), "value": str(queryset.count())}]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        assigned = sum(record.assigned_seats_count for record in records)
        seats = sum(record.seats for record in records)
        chart_data = [
            {"label": _("Assigned Seats"), "value": assigned},
            {"label": _("Available Seats"), "value": max(seats - assigned, 0)},
        ]
        return generate_doughnut_chart(chart_data, title=_("License Seat Utilization"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [{"label": _("Total License Products"), "value": "1 (Mock)"}]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        chart_data = [
            {"label": _("Assigned Seats"), "value": 42},
            {"label": _("Available Seats"), "value": 8},
        ]
        return generate_doughnut_chart(chart_data, title=_("License Seat Utilization"))


register_report_provider(LicenseUtilizationReportProvider())
