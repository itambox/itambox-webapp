"""Provider registry contract tests."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.utils import translation

from assets.models import Asset, AssetAssignment, AssetType, Manufacturer, StatusLabel
from assets.models.lifecycle import Warranty
from core.reports import (
    build_report_context,
    get_registered_report_types,
    get_report_provider,
    register_report_provider,
)
from core.reports.columns import headers_for
from core.reports.contracts import PUBLIC_REPORT_TYPES, ReportDefinition, ReportRequest, ReportResult
from core.reports.formatting import _format_per_currency, _money, _record_currency
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
from organization.models import Location, Site


class ReportProviderContractTests(SimpleTestCase):
    def test_registry_exposes_every_public_report_identifier(self):
        self.assertTrue(set(PUBLIC_REPORT_TYPES).issubset(get_registered_report_types()))

    def test_registry_lookup_returns_provider_per_identifier(self):
        provider = get_report_provider("asset_summary")
        self.assertIsInstance(provider, ReportDefinition)

    def test_registry_rejects_duplicate_registration(self):
        get_registered_report_types()

        class DuplicateProvider(ReportDefinition):
            report_type = "asset_summary"

            def build(self, request):
                return ReportResult(rows=[], summary_cards=[], chart_svg="")

        with self.assertRaises(ImproperlyConfigured):
            register_report_provider(DuplicateProvider())

    def test_registry_does_not_partially_register_a_multi_type_provider(self):
        get_registered_report_types()

        class PartialDuplicateProvider(ReportDefinition):
            report_types = ("uncommitted_report", "asset_summary")

            def build(self, request):
                return ReportResult(rows=[], summary_cards=[], chart_svg="")

        with self.assertRaises(ImproperlyConfigured):
            register_report_provider(PartialDuplicateProvider())
        with self.assertRaises(ValueError):
            get_report_provider("uncommitted_report")

    def test_registry_lookup_unknown_identifier_raises(self):
        with self.assertRaises(ValueError):
            get_report_provider("no_such_report")

    def test_report_provider_permission_is_enforced(self):
        class UserWithoutReportPermission:
            def has_perm(self, permission):
                return False

        template = ReportTemplate(
            name="Unauthorized report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
        )
        with patch("core.reports.orchestration.get_current_user", return_value=UserWithoutReportPermission()):
            with self.assertRaises(PermissionError):
                build_report_context(template, active_tenant=object())

    def test_build_report_context_rejects_unknown_report_type(self):
        template = ReportTemplate(
            name="Unknown Type",
            report_type="no_such_report",
            included_columns=[],
        )
        with self.assertRaises(ValueError):
            build_report_context(template, active_tenant=object())

    def test_headers_for_resolves_lazy_labels_to_plain_strings(self):
        headers = headers_for(["asset_tag", "name", "not_a_column"])
        self.assertEqual(headers, ["Asset Tag", "Asset Name"])
        self.assertTrue(all(isinstance(header, str) for header in headers))


class ReportCurrencyFormattingTests(SimpleTestCase):
    def test_record_currency_prefers_record_then_tenant_then_settings(self):
        from django.test import override_settings

        record = "usd"
        self.assertEqual(_record_currency(record, None), "USD")
        self.assertEqual(_record_currency("", None), "EUR")
        with override_settings(ITAMBOX_DEFAULT_CURRENCY="CHF"):
            self.assertEqual(_record_currency("", None), "CHF")

    def test_money_renders_dash_for_missing_amount(self):
        self.assertEqual(_money(None, "EUR", None), "-")

    def test_format_per_currency_renders_each_currency_and_empty_fallback(self):
        self.assertIn("0", _format_per_currency({}))
        formatted = _format_per_currency({"EUR": Decimal("42.00")})
        self.assertIn("42", formatted)


class AssetSummaryReportProviderTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Provider Tenant", slug="provider-tenant")

    def _build_asset(self, tag, status, asset_type, location, assignment_user=None, **kwargs):
        asset = Asset.objects.create(
            asset_tag=tag,
            name=f"Asset {tag}",
            status=status,
            asset_type=asset_type,
            tenant=self.tenant,
            purchase_cost=Decimal("1200.00"),
            currency="EUR",
            purchase_date=datetime.date(2024, 1, 15),
            **kwargs,
        )
        with self.tenant_context(self.tenant):
            if location or assignment_user:
                AssetAssignment.objects.create(
                    asset=asset,
                    assigned_location=location,
                    assigned_user=assignment_user,
                    is_active=True,
                )
        return asset

    def test_asset_summary_provider_with_real_data_and_all_columns(self):
        manufacturer = Manufacturer.objects.create(name="Provider Maker")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="PM-1", slug="provider-laptop")
        deployed, _ = StatusLabel.objects.get_or_create(name="Deployed", defaults={"slug": "provider-deployed"})
        site = Site.objects.create(name="Provider Site", slug="provider-site")
        location = Location.objects.create(name="Provider HQ", tenant=self.tenant, site=site)
        self._build_asset("PROV-001", deployed, asset_type, location)
        Warranty.objects.create(
            asset=self._build_asset("PROV-002", deployed, asset_type, None),
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )

        template = ReportTemplate(
            name="Provider asset summary",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[
                "asset_tag",
                "name",
                "manufacturer",
                "model",
                "serial_number",
                "status",
                "location",
                "assigned_to",
                "purchase_cost",
                "purchase_date",
                "warranty_months",
            ],
            include_summary_cards=True,
            include_distribution_chart=True,
        )

        with self.tenant_context(self.tenant), translation.override("en"):
            headers, rows, summary_cards, _grouped, chart_svg, _context = build_report_context(
                template, active_tenant=self.tenant
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(headers[-1], "Warranty (Months)")
        self.assertEqual(summary_cards[0]["value"], "2")
        self.assertIn("2,400", str(summary_cards[1]["value"]))
        self.assertIn("<svg", chart_svg)
        by_tag = {row["Asset Tag"]: row for row in rows}
        self.assertEqual(by_tag["PROV-001"]["Manufacturer"], "Provider Maker")
        self.assertEqual(by_tag["PROV-001"]["Location"], "Provider HQ")
        self.assertEqual(by_tag["PROV-002"]["Warranty (Months)"], "36")

    def test_asset_summary_provider_groups_by_status_and_location(self):
        manufacturer = Manufacturer.objects.create(name="Group Maker")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Group Laptop", slug="group-laptop")
        deployed, _ = StatusLabel.objects.get_or_create(name="Deployed", defaults={"slug": "group-deployed"})
        retired, _ = StatusLabel.objects.get_or_create(name="Retired", defaults={"slug": "group-retired"})
        site = Site.objects.create(name="Group Site", slug="group-site")
        location = Location.objects.create(name="Group HQ", tenant=self.tenant, site=site)
        self._build_asset("GRP-001", deployed, asset_type, location)
        self._build_asset("GRP-002", retired, asset_type, None)

        template = ReportTemplate(
            name="Grouped asset summary",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag", "name"],
            include_summary_cards=False,
            include_distribution_chart=False,
            group_by_field="status",
        )
        with self.tenant_context(self.tenant), translation.override("en"):
            _headers, rows, summary_cards, grouped_data, chart_svg, _context = build_report_context(
                template, active_tenant=self.tenant
            )

        self.assertEqual(summary_cards, [])
        self.assertEqual(chart_svg, "")
        self.assertEqual(set(grouped_data), {"Deployed", "Retired"})
        self.assertEqual(len(rows), 2)

        template.group_by_field = "location"
        with self.tenant_context(self.tenant), translation.override("en"):
            _headers, _rows, _cards, grouped_by_location, _chart, _context = build_report_context(
                template, active_tenant=self.tenant
            )
        self.assertEqual(set(grouped_by_location), {"Group HQ", "Unassigned"})

    def test_asset_summary_provider_scopes_by_filter_tenants(self):
        from organization.models import Tenant

        other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        manufacturer = Manufacturer.objects.create(name="Scope Maker")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Scope Laptop", slug="scope-laptop")
        deployed, _ = StatusLabel.objects.get_or_create(name="Deployed", defaults={"slug": "scope-deployed"})
        self._build_asset("SCOPE-001", deployed, asset_type, None)
        Asset.objects.create(
            asset_tag="SCOPE-OTHER",
            name="Other Asset",
            status=deployed,
            asset_type=asset_type,
            tenant=other_tenant,
        )

        template = ReportTemplate(
            name="Scoped asset summary",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )
        self.clear_tenant_context()
        with translation.override("en"):
            _headers, rows, _cards, _grouped, _chart, _context = build_report_context(
                template, active_tenant=None, filter_tenants=[other_tenant]
            )
        self.assertEqual([row["Asset Tag"] for row in rows], ["SCOPE-OTHER"])


class ReportRequestContractTests(SimpleTestCase):
    def test_report_request_contract_fields(self):
        from datetime import datetime
        from datetime import timezone as tz

        template = ReportTemplate(
            name="Contract Template",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )
        request = ReportRequest(
            template=template,
            active_tenant=None,
            filter_tenants=(),
            columns=("asset_tag",),
            user=None,
            as_of=datetime(2026, 1, 1, tzinfo=tz.utc),
        )
        self.assertIsNone(request.active_tenant)
        self.assertEqual(request.filter_tenants, ())
        self.assertEqual(request.columns, ("asset_tag",))
        self.assertEqual(request.as_of.year, 2026)
