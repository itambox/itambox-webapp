"""Characterization matrix for the public report catalogue.

This test deliberately exercises the pre-provider compiler contract for every
public report identifier.  The expected defaults are the compatibility
baseline for provider extraction: identifiers, default columns, summary-card
labels, fallback rows, grouping, and chart output must not drift.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
from subscriptions.models import Provider, Subscription

REPORT_CHARACTERIZATIONS = {
    ReportTemplate.REPORT_TYPE_ASSET_SUMMARY: {
        "columns": ["asset_tag", "name", "status", "location", "assigned_to"],
        "headers": ["Asset Tag", "Asset Name", "Status Label", "Location", "Asset Holder"],
        "summary": ["Total Hardware Assets", "Total Acquisition Sum"],
        "summary_values": ["1 (Mock)", "$3,499.00"],
    },
    ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION: {
        "columns": [
            "license_name",
            "software",
            "seats",
            "assigned_seats",
            "available_seats",
            "utilization_rate",
        ],
        "headers": ["License Name", "Software", "Total Seats", "Assigned Seats", "Available Seats", "Utilization Rate"],
        "summary": ["Total License Products"],
        "summary_values": ["1 (Mock)"],
    },
    ReportTemplate.REPORT_TYPE_SUBSCRIPTION_RENEWALS: {
        "columns": ["subscription_name", "provider", "billing_cycle", "cost", "end_date"],
        "headers": ["Subscription Name", "Provider", "Billing Cycle", "Cost", "End Date"],
        "summary": ["Active Subscriptions", "Est. Monthly Spend"],
        "summary_values": ["1 (Mock)", "$1,200.00"],
    },
    ReportTemplate.REPORT_TYPE_ASSET_MAINTENANCE: {
        "columns": ["maintenance_asset", "maintenance_type", "maintenance_status", "maintenance_cost"],
        "headers": ["Asset", "Type", "Status", "Cost"],
        "summary": ["Total Maintenances", "Total Maintenance Cost"],
        "summary_values": ["1 (Mock)", "$250.00"],
    },
    ReportTemplate.REPORT_TYPE_ASSET_DEPRECIATION: {
        "columns": [
            "asset_tag",
            "name",
            "purchase_cost",
            "salvage_value",
            "depreciation_months",
            "current_value",
        ],
        "headers": [
            "Asset Tag",
            "Asset Name",
            "Purchase Cost",
            "Salvage Value",
            "Depreciation Lifespan (Months)",
            "Depreciated Value",
        ],
        "summary": ["Total Depreciable Assets", "Total Acquisition Cost", "Total Current Book Value"],
        "summary_values": ["1 (Mock)", "$2,500.00", "$1,450.00"],
    },
    ReportTemplate.REPORT_TYPE_SOFTWARE_INVENTORY: {
        "columns": [
            "software_name",
            "manufacturer",
            "version",
            "category",
            "license_type",
            "installed_count",
            "license_count",
        ],
        "headers": [
            "Software Product",
            "Manufacturer",
            "Version",
            "Category",
            "License Type",
            "Installed Count",
            "License Count",
        ],
        "summary": ["Total Software Products"],
        "summary_values": ["1 (Mock)"],
    },
    ReportTemplate.REPORT_TYPE_CONTRACT_RENEWALS: {
        "columns": [
            "contract_number",
            "contract_name",
            "contract_type",
            "contract_status",
            "contract_supplier",
            "contract_end_date",
            "contract_days_until_expiry",
            "contract_cost",
        ],
        "headers": [
            "Contract #",
            "Contract Name",
            "Contract Type",
            "Contract Status",
            "Supplier",
            "End Date",
            "Days Until Expiry",
            "Contract Cost",
        ],
        "summary": ["Active Contracts", "Expiring Within 30 Days", "Est. Annual Spend"],
        "summary_values": ["1 (Mock)", "0 (Mock)", "currency"],
    },
    ReportTemplate.REPORT_TYPE_WARRANTY_EXPIRATION: {
        "columns": [
            "warranty_asset",
            "warranty_type",
            "warranty_provider",
            "warranty_end_date",
            "warranty_days_remaining",
            "warranty_status",
        ],
        "headers": ["Asset", "Warranty Type", "Provider", "End Date", "Days Remaining", "Status"],
        "summary": ["Total Warranties", "Expiring Within 30 Days", "Already Expired", "Total Warranty Cost"],
        "summary_values": ["1 (Mock)", "0 (Mock)", "0 (Mock)", "€299.00"],
    },
    ReportTemplate.REPORT_TYPE_ASSET_DISPOSAL_EOL: {
        "columns": [
            "disposal_asset",
            "disposal_date",
            "disposal_method",
            "disposal_sanitization_method",
            "disposal_weee_compliant",
            "disposal_proceeds",
        ],
        "headers": [
            "Asset",
            "Disposal Date",
            "Disposal Method",
            "Data Sanitization Method",
            "WEEE Compliant",
            "Proceeds",
        ],
        "summary": ["Total Disposals", "WEEE Compliant", "Total Proceeds"],
        "summary_values": ["1 (Mock)", "1 (Mock)", "150,00\u00a0€"],
    },
    ReportTemplate.REPORT_TYPE_HARDWARE_INVENTORY: {
        "columns": [
            "hw_item_type",
            "hw_name",
            "hw_manufacturer",
            "hw_category",
            "hw_total_stock",
            "hw_available",
            "hw_status",
        ],
        "headers": ["Item Type", "Name", "Manufacturer", "Category", "Total Stock", "Available", "Stock Status"],
        "summary": ["Accessory SKUs", "Consumable SKUs", "Component SKUs", "Items at Zero Stock"],
        "summary_values": ["1 (Mock)", "0", "0", "0"],
    },
    ReportTemplate.REPORT_TYPE_CUSTODY_COMPLIANCE: {
        "columns": [
            "custody_asset",
            "custody_holder",
            "custody_status",
            "custody_accepted_date",
            "custody_eula_version",
            "custody_signature_provider",
        ],
        "headers": ["Asset", "Holder", "Acceptance Status", "Accepted Date", "EULA Version", "Signature Provider"],
        "summary": ["Total Receipts", "Pending Sign-offs", "Acceptance Rate"],
        "summary_values": ["1 (Mock)", "0 (Mock)", "100.0% (Mock)"],
    },
}


class ReportCompilerCharacterizationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Report Characterization Tenant", slug="report-characterization")
        self.clear_tenant_context()

    def test_every_public_report_identifier_preserves_compiler_output_contract(self):
        self.assertEqual(
            set(REPORT_CHARACTERIZATIONS),
            {identifier for identifier, _label in ReportTemplate.REPORT_TYPE_CHOICES},
        )

        for report_type, expected in REPORT_CHARACTERIZATIONS.items():
            with self.subTest(report_type=report_type):
                template = ReportTemplate(
                    name=f"Characterization {report_type}",
                    report_type=report_type,
                    included_columns=[],
                    include_summary_cards=True,
                    include_distribution_chart=True,
                )
                headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
                    template, active_tenant=self.tenant
                )

                self.assertEqual(headers, expected["headers"])
                self.assertEqual(list(rows[0]), expected["headers"] + ["_group_by"])
                self.assertEqual(
                    [card["label"] for card in summary_cards],
                    expected["summary"],
                )
                for card, expected_value in zip(summary_cards, expected["summary_values"], strict=False):
                    if expected_value == "currency":
                        self.assertIn("12", str(card["value"]))
                        self.assertIn("€", str(card["value"]))
                    else:
                        self.assertEqual(card["value"], expected_value)
                self.assertEqual(list(grouped_data), ["General"])
                self.assertEqual(grouped_data["General"], rows)
                self.assertIn("<svg", chart_svg)
                self.assertEqual(context_data["headers"], headers)
                self.assertEqual(context_data["grouped_data"], grouped_data)
                self.assertEqual(context_data["summary_cards"], summary_cards)

    def test_subscription_currency_summary_handles_real_rows(self):
        provider = Provider.objects.create(name="Characterization Provider", tenant=self.tenant)
        Subscription.objects.create(
            name="Characterization Subscription",
            provider=provider,
            tenant=self.tenant,
            renewal_date=date.today(),
            renewal_cost=Decimal("120.00"),
            currency="EUR",
            billing_cycle="monthly",
        )
        template = ReportTemplate(
            name="Characterization subscription with data",
            report_type=ReportTemplate.REPORT_TYPE_SUBSCRIPTION_RENEWALS,
            included_columns=[],
            include_summary_cards=True,
            include_distribution_chart=True,
        )

        with self.tenant_context(self.tenant):
            _headers, _rows, summary_cards, _grouped, _chart, _context = compile_report_context(
                template, active_tenant=self.tenant
            )

        self.assertEqual(summary_cards[0]["value"], "1")
        self.assertIn("120", str(summary_cards[1]["value"]))
