"""Software-domain report providers."""

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from core.reports.charts import generate_doughnut_chart
from core.reports.contracts import ReportDefinition, ReportRequest
from core.reports.registry import register_report_provider
from software.models import Software


def _category_label(record):
    return record.get_category_display() if record.category else _("Other")


class SoftwareInventoryReportProvider(ReportDefinition):
    """Software catalogue: each product with its scoped install and licence counts."""

    report_type = "software_inventory"
    permission = "software.view_software"
    #: A null-tenant product is a shared catalogue entry every tenant reports on.
    allow_global_tenant = True
    default_columns = (
        "software_name",
        "manufacturer",
        "version",
        "category",
        "license_type",
        "installed_count",
        "license_count",
    )

    cells = {
        "software_name": lambda record, request: record.name or "-",
        "manufacturer": lambda record, request: record.manufacturer.name if record.manufacturer else "-",
        "version": lambda record, request: record.version or "-",
        "category": lambda record, request: record.get_category_display() if record.category else "-",
        "license_type": lambda record, request: record.get_license_type_display() if record.license_type else "-",
        "installed_count": lambda record, request: str(record.scoped_installed_count),
        "license_count": lambda record, request: str(record.scoped_license_count),
    }

    sample_cells = {
        "software_name": "Office 365 E5 (Mock)",
        "manufacturer": "Microsoft",
        "version": "16.0",
        "category": "Productivity",
        "license_type": "Subscription",
        "installed_count": "25",
        "license_count": "30",
    }

    group_resolvers = {
        "category": lambda record, request: _category_label(record),
        "manufacturer": lambda record, request: record.manufacturer.name if record.manufacturer else _("Generic"),
    }

    sample_group_keys = {"category": "Productivity", "manufacturer": "Microsoft"}

    def _scoped_queryset(self, request: ReportRequest):
        return self.scope_to_tenants(Software.objects.all().select_related("manufacturer"), request)

    def get_queryset(self, request: ReportRequest):
        queryset = self._scoped_queryset(request)
        # The model's own install/licence properties derive their scope from the
        # ambient tenant context, which is unreliable in a scheduled/MSP run.
        # Annotate both counts explicitly against the report's tenants in one
        # query instead: distinct=True keeps each count correct despite the join
        # fan-out, and the filters mirror the row scoping above so the figures
        # never include another tenant's installs or licences.
        tenant_ids = self._report_tenant_ids(request)
        if tenant_ids is None:
            installed_count = Count("installed_instances", distinct=True)
            license_count = Count("licenses", filter=Q(licenses__deleted_at__isnull=True), distinct=True)
        else:
            installed_count = Count(
                "installed_instances",
                filter=Q(installed_instances__asset__tenant_id__in=tenant_ids),
                distinct=True,
            )
            license_count = Count(
                "licenses",
                filter=Q(licenses__deleted_at__isnull=True) & Q(licenses__tenant_id__in=tenant_ids),
                distinct=True,
            )
        return queryset.annotate(scoped_installed_count=installed_count, scoped_license_count=license_count)

    @staticmethod
    def _report_tenant_ids(request: ReportRequest):
        """The tenant ids the counts are restricted to, or ``None`` when global."""
        if request.filter_tenants:
            return [tenant.pk for tenant in request.filter_tenants]
        if request.active_tenant:
            return [request.active_tenant.pk]
        return None

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(record, request) for record in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [{"label": _("Total Software Products"), "value": str(self._scoped_queryset(request).count())}]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        category_counts = {}
        for record in records:
            label = _category_label(record)
            category_counts[label] = category_counts.get(label, 0) + 1
        chart_data = [{"label": label, "value": count} for label, count in category_counts.items()]
        return generate_doughnut_chart(chart_data, title=_("Software Category Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [{"label": _("Total Software Products"), "value": "1 (Mock)"}]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart(
            [{"label": "Productivity", "value": 1}], title=_("Software Category Distribution")
        )


register_report_provider(SoftwareInventoryReportProvider())
