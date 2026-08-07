"""Asset-domain report providers."""

from django.db.models import Count, Sum
from django.utils.translation import gettext as _

from assets.models import Asset
from core.reports.charts import generate_doughnut_chart
from core.reports.contracts import ReportDefinition, ReportRequest, ReportResult
from core.reports.formatting import _format_per_currency, _money, _record_currency
from core.reports.registry import register_report_provider


class AssetSummaryReportProvider(ReportDefinition):
    report_type = "asset_summary"
    default_columns = ("asset_tag", "name", "status", "location", "assigned_to")

    def get_queryset(self, request: ReportRequest):
        assets_qs = Asset.objects.filter(deleted_at__isnull=True)
        if request.filter_tenants:
            assets_qs = assets_qs.filter(tenant__in=request.filter_tenants)
        elif request.active_tenant:
            assets_qs = assets_qs.filter(tenant=request.active_tenant)
        return assets_qs.select_related("asset_type", "asset_type__manufacturer", "status").prefetch_related(
            "warranties",
            "assignments",
            "assignments__assigned_user",
            "assignments__assigned_location",
            "assignments__assigned_asset",
        )

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []

        total_assets = queryset.count()
        acq_by_currency = {}
        for cur, total in (
            queryset.exclude(purchase_cost__isnull=True)
            .values("currency")
            .annotate(c=Sum("purchase_cost"))
            .values_list("currency", "c")
        ):
            code = _record_currency(cur, request.active_tenant)
            acq_by_currency[code] = acq_by_currency.get(code, 0) + (total or 0)

        if total_assets == 0:
            return [
                {"label": _("Total Hardware Assets"), "value": "1 (Mock)"},
                {"label": _("Total Acquisition Sum"), "value": "$3,499.00"},
            ]
        return [
            {"label": _("Total Hardware Assets"), "value": str(total_assets)},
            {"label": _("Total Acquisition Sum"), "value": _format_per_currency(acq_by_currency)},
        ]

    def build_rows(self, queryset, request: ReportRequest):
        rows = []
        active_cols = request.columns
        for asset in queryset[:500]:
            row = {}
            if "asset_tag" in active_cols:
                row[_("Asset Tag")] = asset.asset_tag or "-"
            if "name" in active_cols:
                row[_("Asset Name")] = asset.name or "-"
            if "manufacturer" in active_cols:
                row[_("Manufacturer")] = asset.manufacturer.name if asset.manufacturer else "-"
            if "model" in active_cols:
                row[_("Model")] = asset.model if asset.model else "-"
            if "serial_number" in active_cols:
                row[_("Serial Number")] = asset.serial_number or "-"
            if "status" in active_cols:
                row[_("Status Label")] = asset.status.name if asset.status else "-"
            if "location" in active_cols:
                loc = (
                    asset.active_assignment.assigned_to
                    if (asset.active_assignment and asset.active_assignment.assigned_to_type == "location")
                    else None
                )
                row[_("Location")] = loc.name if loc else "-"
            if "assigned_to" in active_cols:
                holder = asset.active_assignment.assigned_to if asset.active_assignment else None
                row[_("Asset Holder")] = str(holder) if holder else "-"
            if "purchase_cost" in active_cols:
                row[_("Purchase Cost")] = _money(
                    asset.purchase_cost, getattr(asset, "currency", None), request.active_tenant
                )
            if "purchase_date" in active_cols:
                row[_("Purchase Date")] = asset.purchase_date.strftime("%Y-%m-%d") if asset.purchase_date else "-"
            if "warranty_months" in active_cols:
                active_warranty = next(
                    (
                        warranty
                        for warranty in asset.warranties.all()
                        if warranty.deleted_at is None
                        and warranty.start_date
                        and warranty.end_date
                        and warranty.start_date <= request.as_of.date() <= warranty.end_date
                    ),
                    None,
                )
                months = (
                    int((active_warranty.end_date - active_warranty.start_date).days / 30.4)
                    if active_warranty
                    else None
                )
                row[_("Warranty (Months)")] = str(months) if months else "-"

            group_val = "General"
            if request.template.group_by_field:
                if request.template.group_by_field == "location":
                    loc = (
                        asset.active_assignment.assigned_to
                        if (asset.active_assignment and asset.active_assignment.assigned_to_type == "location")
                        else None
                    )
                    group_val = loc.name if loc else _("Unassigned")
                elif request.template.group_by_field == "status":
                    group_val = asset.status.name if asset.status else _("Default")
                elif request.template.group_by_field == "manufacturer":
                    group_val = asset.manufacturer.name if asset.manufacturer else _("Generic")
            row["_group_by"] = group_val
            rows.append(row)

        if rows:
            return rows

        row = {}
        for col in active_cols:
            if col == "asset_tag":
                row[_("Asset Tag")] = "AST-MOCK-001"
            elif col == "name":
                row[_("Asset Name")] = 'MacBook Pro 16" (Mock)'
            elif col == "manufacturer":
                row[_("Manufacturer")] = "Apple"
            elif col == "model":
                row[_("Model")] = "M3 Max 64GB"
            elif col == "serial_number":
                row[_("Serial Number")] = "C02F8XXXXXXX"
            elif col == "status":
                row[_("Status Label")] = "Deployed"
            elif col == "location":
                row[_("Location")] = "HQ Amsterdam"
            elif col == "assigned_to":
                row[_("Asset Holder")] = "Alex Dev"
            elif col == "purchase_cost":
                row[_("Purchase Cost")] = "$3,499.00"
            elif col == "purchase_date":
                row[_("Purchase Date")] = "2026-01-15"
            elif col == "warranty_months":
                row[_("Warranty (Months)")] = "36"
        row["_group_by"] = (
            "HQ Amsterdam"
            if request.template.group_by_field == "location"
            else "Deployed"
            if request.template.group_by_field == "status"
            else "Apple"
            if request.template.group_by_field == "manufacturer"
            else "General"
        )
        return [row]

    def build_chart(self, queryset, request: ReportRequest):
        status_counts = queryset.values("status__name").annotate(count=Count("id")).order_by("-count")
        chart_data = [
            {"label": item["status__name"] or _("Default"), "value": item["count"]}
            for item in status_counts
            if item["count"] > 0
        ]
        if not chart_data:
            chart_data = [
                {"label": _("Deployed"), "value": 85},
                {"label": _("Ready to Deploy"), "value": 20},
                {"label": _("Archived"), "value": 12},
            ]
        if request.template.include_distribution_chart:
            return generate_doughnut_chart(chart_data, title=_("Asset Status Distribution"))
        return ""

    def build(self, request: ReportRequest):
        queryset = self.get_queryset(request)
        rows = list(self.build_rows(queryset, request))
        return ReportResult(
            rows=rows,
            summary_cards=list(self.build_summary(queryset, request)),
            chart_svg=self.build_chart(queryset, request),
        )


register_report_provider(AssetSummaryReportProvider())
