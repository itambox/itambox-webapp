"""Asset-domain report providers.

Five public report identifiers are owned here: the inventory summary, the
maintenance ledger, the depreciation schedule, warranty expiration, and
disposal/end-of-life.  Each declares the records it reads, what its cells
render, and how its summary and chart are derived; the shared orchestration in
:mod:`core.reports` only groups and renders what they hand back.
"""

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from assets.models import Asset, AssetMaintenance
from assets.models.lifecycle import AssetDisposal, Warranty
from core.reports.charts import generate_doughnut_chart
from core.reports.contracts import ReportDefinition, ReportRequest, ReportResult
from core.reports.formatting import _format_per_currency, _money, _record_currency
from core.reports.registry import register_report_provider

# -- asset summary --------------------------------------------------------


def _assignment_location(asset):
    """The location an asset is currently assigned to, or ``None``."""
    assignment = asset.active_assignment
    if assignment and assignment.assigned_to_type == "location":
        return assignment.assigned_to
    return None


def _location_cell(asset, request):
    location = _assignment_location(asset)
    return location.name if location else "-"


def _holder_cell(asset, request):
    assignment = asset.active_assignment
    holder = assignment.assigned_to if assignment else None
    return str(holder) if holder else "-"


def _warranty_months_cell(asset, request):
    """The term of the warranty covering ``as_of``, from the prefetched relation."""
    today = timezone.localdate(request.as_of)
    warranty = next(
        (
            candidate
            for candidate in asset.warranties.all()
            if candidate.deleted_at is None
            and candidate.start_date
            and candidate.end_date
            and candidate.start_date <= today <= candidate.end_date
        ),
        None,
    )
    if warranty is None:
        return "-"
    months = int((warranty.end_date - warranty.start_date).days / 30.4)
    return str(months) if months else "-"


def _location_group(asset, request):
    location = _assignment_location(asset)
    return location.name if location else _("Unassigned")


class AssetSummaryReportProvider(ReportDefinition):
    """Hardware inventory: one row per asset, with its status and holder."""

    report_type = "asset_summary"
    permission = "assets.view_asset"
    default_columns = ("asset_tag", "name", "status", "location", "assigned_to")

    cells = {
        "asset_tag": lambda asset, request: asset.asset_tag or "-",
        "name": lambda asset, request: asset.name or "-",
        "manufacturer": lambda asset, request: asset.manufacturer.name if asset.manufacturer else "-",
        "model": lambda asset, request: asset.model if asset.model else "-",
        "serial_number": lambda asset, request: asset.serial_number or "-",
        "status": lambda asset, request: asset.status.name if asset.status else "-",
        "location": _location_cell,
        "assigned_to": _holder_cell,
        "purchase_cost": lambda asset, request: _money(
            asset.purchase_cost, getattr(asset, "currency", None), request.active_tenant
        ),
        "purchase_date": lambda asset, request: (
            asset.purchase_date.strftime("%Y-%m-%d") if asset.purchase_date else "-"
        ),
        "warranty_months": _warranty_months_cell,
    }

    sample_cells = {
        "asset_tag": "AST-MOCK-001",
        "name": 'MacBook Pro 16" (Mock)',
        "manufacturer": "Apple",
        "model": "M3 Max 64GB",
        "serial_number": "C02F8XXXXXXX",
        "status": "Deployed",
        "location": "HQ Amsterdam",
        "assigned_to": "Alex Dev",
        "purchase_cost": "$3,499.00",
        "purchase_date": "2026-01-15",
        "warranty_months": "36",
    }

    group_resolvers = {
        "location": _location_group,
        "status": lambda asset, request: asset.status.name if asset.status else _("Default"),
        "manufacturer": lambda asset, request: asset.manufacturer.name if asset.manufacturer else _("Generic"),
    }

    sample_group_keys = {
        "location": "HQ Amsterdam",
        "status": "Deployed",
        "manufacturer": "Apple",
    }

    def get_queryset(self, request: ReportRequest):
        queryset = self.scope_to_tenants(Asset.objects.filter(deleted_at__isnull=True), request)
        return queryset.select_related("asset_type", "asset_type__manufacturer", "status").prefetch_related(
            "warranties",
            "assignments",
            "assignments__assigned_user",
            "assignments__assigned_location",
            "assignments__assigned_asset",
        )

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(asset, request) for asset in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        total_assets = queryset.count()
        # Each asset carries its own currency and there is no FX source, so a
        # single combined acquisition sum would be meaningless.
        acquisition_by_currency = {}
        for currency, total in (
            queryset.exclude(purchase_cost__isnull=True)
            .values("currency")
            .annotate(total=Sum("purchase_cost"))
            .values_list("currency", "total")
        ):
            code = _record_currency(currency, request.active_tenant)
            acquisition_by_currency[code] = acquisition_by_currency.get(code, 0) + (total or 0)

        return [
            {"label": _("Total Hardware Assets"), "value": str(total_assets)},
            {"label": _("Total Acquisition Sum"), "value": _format_per_currency(acquisition_by_currency)},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        status_counts = queryset.values("status__name").annotate(count=Count("id")).order_by("-count")
        chart_data = [
            {"label": item["status__name"] or _("Default"), "value": item["count"]}
            for item in status_counts
            if item["count"] > 0
        ]
        return generate_doughnut_chart(chart_data, title=_("Asset Status Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Hardware Assets"), "value": "1 (Mock)"},
            {"label": _("Total Acquisition Sum"), "value": "$3,499.00"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart(
            [
                {"label": _("Deployed"), "value": 85},
                {"label": _("Ready to Deploy"), "value": 20},
                {"label": _("Archived"), "value": 12},
            ],
            title=_("Asset Status Distribution"),
        )


# -- asset maintenance ----------------------------------------------------


class AssetMaintenanceReportProvider(ReportDefinition):
    """Maintenance ledger: one row per maintenance record on a scoped asset."""

    report_type = "asset_maintenance"
    permission = "assets.view_assetmaintenance"
    tenant_field = "asset__tenant"
    default_columns = ("maintenance_asset", "maintenance_type", "maintenance_status", "maintenance_cost")

    cells = {
        "maintenance_asset": lambda maintenance, request: maintenance.asset.name if maintenance.asset else "-",
        "maintenance_type": lambda maintenance, request: maintenance.get_maintenance_type_display(),
        "maintenance_status": lambda maintenance, request: maintenance.get_status_display(),
        "maintenance_cost": lambda maintenance, request: _money(
            maintenance.cost, getattr(maintenance, "currency", None), request.active_tenant
        ),
        "maintenance_start_date": lambda maintenance, request: (
            maintenance.start_date.strftime("%Y-%m-%d") if maintenance.start_date else "-"
        ),
        "maintenance_completion_date": lambda maintenance, request: (
            maintenance.completion_date.strftime("%Y-%m-%d") if maintenance.completion_date else "-"
        ),
        "maintenance_downtime": lambda maintenance, request: (
            str(maintenance.downtime_days) if maintenance.downtime_days is not None else "-"
        ),
    }

    sample_cells = {
        "maintenance_asset": 'MacBook Pro 16"',
        "maintenance_type": "Repair",
        "maintenance_status": "Completed",
        "maintenance_cost": "$250.00",
        "maintenance_start_date": "2026-05-01",
        "maintenance_completion_date": "2026-05-05",
        "maintenance_downtime": "4",
    }

    group_resolvers = {
        "status": lambda maintenance, request: maintenance.get_status_display(),
        "maintenance_type": lambda maintenance, request: maintenance.get_maintenance_type_display(),
        "asset": lambda maintenance, request: maintenance.asset.name if maintenance.asset else _("Unassigned"),
    }

    sample_group_keys = {
        "status": "Completed",
        "maintenance_type": "Repair",
        "asset": 'MacBook Pro 16"',
    }

    def get_queryset(self, request: ReportRequest):
        queryset = AssetMaintenance.objects.filter(deleted_at__isnull=True).select_related("asset", "supplier")
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(maintenance, request) for maintenance in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        total_maintenances = queryset.count()
        # Each maintenance record carries its own currency; bucket, never sum.
        cost_by_currency = {}
        for maintenance in queryset:
            if maintenance.cost:
                code = _record_currency(getattr(maintenance, "currency", None), request.active_tenant)
                cost_by_currency[code] = cost_by_currency.get(code, 0) + maintenance.cost

        return [
            {"label": _("Total Maintenances"), "value": str(total_maintenances)},
            {"label": _("Total Maintenance Cost"), "value": _format_per_currency(cost_by_currency)},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        type_counts = {}
        for maintenance in records:
            label = maintenance.get_maintenance_type_display()
            type_counts[label] = type_counts.get(label, 0) + 1
        chart_data = [{"label": label, "value": count} for label, count in type_counts.items()]
        return generate_doughnut_chart(chart_data, title=_("Maintenance Type Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Maintenances"), "value": "1 (Mock)"},
            {"label": _("Total Maintenance Cost"), "value": "$250.00"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart([{"label": "Repair", "value": 1}], title=_("Maintenance Type Distribution"))


# -- asset depreciation ---------------------------------------------------


def _depreciation_months(asset, request):
    depreciation = asset.asset_type.depreciation if asset.asset_type else None
    months = depreciation.months if depreciation else None
    return str(months) if months else "-"


def _depreciation_totals(queryset, active_tenant):
    """Acquisition and book-value totals for the whole scope, per currency.

    Returns the two combined totals the chart splits on plus the per-currency
    buckets the summary cards render; the queryset is walked once because
    ``current_value`` is a model property and cannot be aggregated in SQL.
    """
    total_purchase_cost = 0
    total_current_value = 0
    purchase_by_currency = {}
    value_by_currency = {}
    for asset in queryset:
        code = _record_currency(getattr(asset, "currency", None), active_tenant)
        if asset.purchase_cost:
            total_purchase_cost += asset.purchase_cost
            purchase_by_currency[code] = purchase_by_currency.get(code, 0) + asset.purchase_cost
        if asset.current_value is not None:
            total_current_value += asset.current_value
            value_by_currency[code] = value_by_currency.get(code, 0) + asset.current_value
    return total_purchase_cost, total_current_value, purchase_by_currency, value_by_currency


class AssetDepreciationReportProvider(ReportDefinition):
    """Depreciation schedule: acquisition cost against current book value."""

    report_type = "asset_depreciation"
    permission = "assets.view_asset"
    default_columns = (
        "asset_tag",
        "name",
        "purchase_cost",
        "salvage_value",
        "depreciation_months",
        "current_value",
    )

    cells = {
        "asset_tag": lambda asset, request: asset.asset_tag or "-",
        "name": lambda asset, request: asset.name or "-",
        "purchase_cost": lambda asset, request: _money(
            asset.purchase_cost, getattr(asset, "currency", None), request.active_tenant
        ),
        "salvage_value": lambda asset, request: _money(
            asset.salvage_value, getattr(asset, "currency", None), request.active_tenant
        ),
        "depreciation_months": _depreciation_months,
        "current_value": lambda asset, request: _money(
            asset.current_value, getattr(asset, "currency", None), request.active_tenant
        ),
    }

    sample_cells = {
        "asset_tag": "AST-MOCK-001",
        "name": "Developer Workstation (Mock)",
        "purchase_cost": "$2,500.00",
        "salvage_value": "$200.00",
        "depreciation_months": "36",
        "current_value": "$1,450.00",
    }

    group_resolvers = {
        "status": lambda asset, request: asset.status.name if asset.status else _("Default"),
        "depreciation": lambda asset, request: (
            asset.asset_type.depreciation.name
            if (asset.asset_type and asset.asset_type.depreciation)
            else _("No Scheme")
        ),
    }

    def get_queryset(self, request: ReportRequest):
        queryset = self.scope_to_tenants(Asset.objects.filter(deleted_at__isnull=True), request)
        return queryset.select_related("asset_type", "asset_type__depreciation", "status")

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(asset, request) for asset in records]

    def _summary_from_totals(self, queryset, request: ReportRequest, totals):
        _purchase_total, _value_total, purchase_by_currency, value_by_currency = totals
        return [
            {"label": _("Total Depreciable Assets"), "value": str(queryset.count())},
            {"label": _("Total Acquisition Cost"), "value": _format_per_currency(purchase_by_currency)},
            {"label": _("Total Current Book Value"), "value": _format_per_currency(value_by_currency)},
        ]

    def _chart_from_totals(self, request: ReportRequest, totals):
        purchase_total, value_total, _purchase_by_currency, _value_by_currency = totals
        # Assets can be in scope without a recorded acquisition cost; the split
        # is meaningless then, so the illustrative one is shown instead.
        if purchase_total > 0:
            chart_data = [
                {"label": _("Depreciated Book Value"), "value": float(value_total)},
                {"label": _("Depreciated Amount"), "value": max(float(purchase_total - value_total), 0.0)},
            ]
        else:
            chart_data = _sample_depreciation_chart_data()
        return generate_doughnut_chart(chart_data, title=_("Asset Value Depreciation"))

    def build(self, request: ReportRequest) -> ReportResult:
        queryset = self.get_queryset(request)
        records = list(queryset[: self.row_limit])
        rows = list(self.build_rows(records, request))
        if not rows:
            return self.build_sample(request)
        totals = None
        if request.template.include_summary_cards or request.template.include_distribution_chart:
            totals = _depreciation_totals(queryset, request.active_tenant)
        return ReportResult(
            rows=rows,
            summary_cards=self._summary_from_totals(queryset, request, totals)
            if request.template.include_summary_cards
            else [],
            chart_svg=self._chart_from_totals(request, totals) if request.template.include_distribution_chart else "",
        )

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return self._summary_from_totals(queryset, request, _depreciation_totals(queryset, request.active_tenant))

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return self._chart_from_totals(request, _depreciation_totals(queryset, request.active_tenant))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Depreciable Assets"), "value": "1 (Mock)"},
            {"label": _("Total Acquisition Cost"), "value": "$2,500.00"},
            {"label": _("Total Current Book Value"), "value": "$1,450.00"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart(_sample_depreciation_chart_data(), title=_("Asset Value Depreciation"))


def _sample_depreciation_chart_data():
    return [
        {"label": _("Depreciated Book Value"), "value": 1450.0},
        {"label": _("Depreciated Amount"), "value": 1050.0},
    ]


# -- warranty expiration --------------------------------------------------

#: A warranty inside this many days of its end date reads as expiring soon.
WARRANTY_SOON_DAYS = 30


def _warranty_state(warranty, request):
    """``(days_remaining, status_label)`` for one warranty at the report's clock."""
    if warranty.end_date is None:
        return "-", _("Unknown")
    delta = (warranty.end_date - timezone.localdate(request.as_of)).days
    if delta < 0:
        return str(delta), _("Expired")
    if delta <= WARRANTY_SOON_DAYS:
        return str(delta), _("Expiring Soon")
    return str(delta), _("Active")


class WarrantyExpirationReportProvider(ReportDefinition):
    """Warranty expiration: remaining term and status per warranty."""

    report_type = "warranty_expiration"
    permission = "assets.view_warranty"
    tenant_field = "asset__tenant"
    default_columns = (
        "warranty_asset",
        "warranty_type",
        "warranty_provider",
        "warranty_end_date",
        "warranty_days_remaining",
        "warranty_status",
    )

    cells = {
        "warranty_asset": lambda warranty, request: warranty.asset.name if warranty.asset else "-",
        "warranty_type": lambda warranty, request: warranty.get_warranty_type_display(),
        "warranty_provider": lambda warranty, request: warranty.provider or "-",
        "warranty_start_date": lambda warranty, request: (
            warranty.start_date.strftime("%Y-%m-%d") if warranty.start_date else "-"
        ),
        "warranty_end_date": lambda warranty, request: (
            warranty.end_date.strftime("%Y-%m-%d") if warranty.end_date else "-"
        ),
        "warranty_days_remaining": lambda warranty, request: _warranty_state(warranty, request)[0],
        "warranty_status": lambda warranty, request: _warranty_state(warranty, request)[1],
        "warranty_cost": lambda warranty, request: _money(
            warranty.cost, getattr(warranty, "currency", None), request.active_tenant
        ),
        "warranty_reference": lambda warranty, request: warranty.reference or "-",
    }

    sample_cells = {
        "warranty_asset": 'MacBook Pro 16" (Mock)',
        "warranty_type": "Hardware",
        "warranty_provider": "Apple Care+",
        "warranty_start_date": "2024-01-15",
        "warranty_end_date": "2027-01-14",
        "warranty_days_remaining": "935",
        "warranty_status": "Active",
        "warranty_cost": "€299.00",
        "warranty_reference": "REF-MOCK-0001",
    }

    group_resolvers = {
        "warranty_type": lambda warranty, request: warranty.get_warranty_type_display(),
        "status": lambda warranty, request: _warranty_state(warranty, request)[1],
        "asset": lambda warranty, request: warranty.asset.name if warranty.asset else _("Unassigned"),
    }

    sample_group_keys = {
        "warranty_type": "Hardware",
        "status": "Active",
        "asset": 'MacBook Pro 16" (Mock)',
    }

    def get_queryset(self, request: ReportRequest):
        queryset = Warranty.objects.filter(deleted_at__isnull=True).select_related("asset")
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(warranty, request) for warranty in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        today = timezone.localdate(request.as_of)
        threshold = today + timedelta(days=WARRANTY_SOON_DAYS)
        cost_by_currency = {}
        for warranty in queryset:
            if warranty.cost is not None:
                code = _record_currency(getattr(warranty, "currency", None), request.active_tenant)
                cost_by_currency[code] = cost_by_currency.get(code, 0) + warranty.cost

        return [
            {"label": _("Total Warranties"), "value": str(queryset.count())},
            {
                "label": _("Expiring Within 30 Days"),
                "value": str(queryset.filter(end_date__gte=today, end_date__lte=threshold).count()),
            },
            {"label": _("Already Expired"), "value": str(queryset.filter(end_date__lt=today).count())},
            {"label": _("Total Warranty Cost"), "value": _format_per_currency(cost_by_currency)},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        status_counts = {}
        for warranty in records:
            _days, status_label = _warranty_state(warranty, request)
            status_counts[status_label] = status_counts.get(status_label, 0) + 1
        chart_data = [{"label": label, "value": count} for label, count in status_counts.items()]
        return generate_doughnut_chart(chart_data, title=_("Warranty Status Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Warranties"), "value": "1 (Mock)"},
            {"label": _("Expiring Within 30 Days"), "value": "0 (Mock)"},
            {"label": _("Already Expired"), "value": "0 (Mock)"},
            {"label": _("Total Warranty Cost"), "value": "€299.00"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart([{"label": _("Active"), "value": 1}], title=_("Warranty Status Distribution"))


# -- asset disposal / end-of-life -----------------------------------------


class AssetDisposalEolReportProvider(ReportDefinition):
    """Disposal & end-of-life: how retired assets left the estate."""

    report_type = "asset_disposal_eol"
    permission = "assets.view_assetdisposal"
    tenant_field = "asset__tenant"
    default_columns = (
        "disposal_asset",
        "disposal_date",
        "disposal_method",
        "disposal_sanitization_method",
        "disposal_weee_compliant",
        "disposal_proceeds",
    )

    cells = {
        "disposal_asset": lambda disposal, request: str(disposal.asset) if disposal.asset else "-",
        "disposal_date": lambda disposal, request: (
            disposal.disposal_date.strftime("%Y-%m-%d") if disposal.disposal_date else "-"
        ),
        "disposal_method": lambda disposal, request: disposal.get_disposal_method_display(),
        "disposal_sanitization_method": lambda disposal, request: disposal.get_data_sanitization_method_display(),
        "disposal_sanitization_certificate": lambda disposal, request: disposal.sanitization_certificate or "-",
        "disposal_sanitized_by": lambda disposal, request: disposal.sanitized_by or "-",
        "disposal_recipient": lambda disposal, request: disposal.recipient or "-",
        "disposal_proceeds": lambda disposal, request: _money(
            disposal.proceeds, getattr(disposal, "currency", None), request.active_tenant
        ),
        "disposal_weee_compliant": lambda disposal, request: _("Yes") if disposal.weee_compliant else _("No"),
        "disposal_notes": lambda disposal, request: disposal.notes or "-",
    }

    sample_cells = {
        "disposal_asset": "ASSET-MOCK-001 (Mock)",
        "disposal_date": "2026-06-01",
        "disposal_method": "Recycle / WEEE",
        "disposal_sanitization_method": "NIST Purge (cryptographic or ATA Secure Erase)",
        "disposal_sanitization_certificate": "CERT-2026-001",
        "disposal_sanitized_by": "SecureWipe GmbH",
        "disposal_recipient": "GreenIT Recyclers",
        "disposal_proceeds": "150,00\xa0€",
        "disposal_weee_compliant": "Yes",
        "disposal_notes": "-",
    }

    group_resolvers = {
        "disposal_method": lambda disposal, request: disposal.get_disposal_method_display(),
        "disposal_sanitization_method": (lambda disposal, request: disposal.get_data_sanitization_method_display()),
        "disposal_weee_compliant": lambda disposal, request: (
            _("WEEE Compliant") if disposal.weee_compliant else _("Not WEEE Compliant")
        ),
    }

    sample_group_keys = {
        "disposal_method": "Recycle / WEEE",
        "disposal_sanitization_method": "NIST Purge (cryptographic or ATA Secure Erase)",
        "disposal_weee_compliant": "WEEE Compliant",
    }

    def get_queryset(self, request: ReportRequest):
        queryset = AssetDisposal.objects.filter(deleted_at__isnull=True).select_related("asset", "asset__tenant")
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(disposal, request) for disposal in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        proceeds_by_currency = {}
        for disposal in queryset:
            if disposal.proceeds is not None:
                code = _record_currency(getattr(disposal, "currency", None), request.active_tenant)
                proceeds_by_currency[code] = proceeds_by_currency.get(code, 0) + disposal.proceeds

        return [
            {"label": _("Total Disposals"), "value": str(queryset.count())},
            {"label": _("WEEE Compliant"), "value": str(queryset.filter(weee_compliant=True).count())},
            {"label": _("Total Proceeds"), "value": _format_per_currency(proceeds_by_currency)},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        method_counts = {}
        for disposal in records:
            label = disposal.get_disposal_method_display()
            method_counts[label] = method_counts.get(label, 0) + 1
        chart_data = [{"label": label, "value": count} for label, count in method_counts.items()]
        return generate_doughnut_chart(chart_data, title=_("Disposal Method Distribution"))

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Total Disposals"), "value": "1 (Mock)"},
            {"label": _("WEEE Compliant"), "value": "1 (Mock)"},
            {"label": _("Total Proceeds"), "value": "150,00\xa0€"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        return generate_doughnut_chart(
            [{"label": "Recycle / WEEE", "value": 1}], title=_("Disposal Method Distribution")
        )


register_report_provider(AssetSummaryReportProvider())
register_report_provider(AssetMaintenanceReportProvider())
register_report_provider(AssetDepreciationReportProvider())
register_report_provider(WarrantyExpirationReportProvider())
register_report_provider(AssetDisposalEolReportProvider())
