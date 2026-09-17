"""#496 language review item 1: report totals must count ACTIVE disposals only.

Historical rows (including cancelled ones) stay in the report output - that is the
evidence - but the effective summary cards and the method distribution may only reflect
records that still own their asset. Cancelled proceeds must never be reported as proceeds.
"""

from decimal import Decimal

from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, StatusLabel
from assets.models.choices import DataSanitizationMethodChoices, DisposalMethodChoices
from assets.models.lifecycle import AssetDisposal
from assets.services import cancel_asset_disposal
from core.reports import build_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
from organization.models import Tenant


class DisposalReportEffectiveTotalsTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Report Tenant", slug="report-tenant")
        self.other_tenant = Tenant.objects.create(name="Report Other", slug="report-other")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)
        self.set_active_tenant(self.tenant)
        self.template = baker.make(
            ReportTemplate,
            report_type="asset_disposal_eol",
            include_summary_cards=True,
            include_distribution_chart=True,
            tenant=self.tenant,
        )

    def _disposal(self, tag, method, proceeds, currency, weee, cancelled=False):
        asset = baker.make(Asset, name=f"Report asset {tag}", asset_tag=tag, tenant=self.tenant, status=self.status)
        record = AssetDisposal.objects.create(
            asset=asset,
            disposal_date="2026-05-15",
            disposal_method=method,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_PURGE,
            proceeds=proceeds,
            currency=currency,
            weee_compliant=weee,
        )
        if cancelled:
            cancel_asset_disposal(record, user=self.tenant_user, reason="recorded in error")
            record.refresh_from_db()
        return record

    def _cards(self):
        _headers, rows, cards, _grouped, chart_svg, _context = build_report_context(
            self.template, active_tenant=self.tenant
        )
        return {card["label"]: card["value"] for card in cards}, rows, chart_svg

    def test_effective_totals_and_chart_ignore_cancelled_records(self):
        self._disposal("RPT-A", DisposalMethodChoices.DONATION, Decimal("100.00"), "EUR", True)
        self._disposal("RPT-B", DisposalMethodChoices.DONATION, Decimal("50.00"), "USD", True)
        cancelled = self._disposal(
            "RPT-C", DisposalMethodChoices.RECYCLE, Decimal("999.00"), "EUR", True, cancelled=True
        )

        cards, rows, chart_svg = self._cards()

        self.assertEqual(cards["Active Disposals"], "2")
        self.assertEqual(cards["Active WEEE-compliant disposals"], "2")
        self.assertNotIn("999", cards["Proceeds from active disposals"], "cancelled proceeds must not be reported")
        # both active currencies survive the formatting (symbol form, not code)
        self.assertIn("\u20ac", cards["Proceeds from active disposals"])
        self.assertIn("$", cards["Proceeds from active disposals"])

        # Historical evidence stays visible in the row output: the cancelled record is
        # still listed and marked as cancelled by the Disposal Status column.
        asset_cells = [row.get("Asset") or "" for row in rows]
        self.assertTrue(
            any(cancelled.asset.asset_tag in cell for cell in asset_cells),
            f"the cancelled record must stay in the report rows: {asset_cells}",
        )
        self.assertIsNotNone(
            AssetDisposal.all_objects.get(pk=cancelled.pk).cancelled_at,
            "the cancellation evidence must survive",
        )

        # Chart inputs: the active method only.
        self.assertIn(str(DisposalMethodChoices.DONATION.label), chart_svg)
        self.assertNotIn(str(DisposalMethodChoices.RECYCLE.label), chart_svg)

    def test_cancelled_only_history_reports_zero_effective_totals(self):
        cancelled = self._disposal(
            "RPT-D", DisposalMethodChoices.RECYCLE, Decimal("999.00"), "EUR", True, cancelled=True
        )

        cards, rows, chart_svg = self._cards()

        self.assertEqual(cards["Active Disposals"], "0")
        self.assertEqual(cards["Active WEEE-compliant disposals"], "0")
        self.assertNotIn("999", cards["Proceeds from active disposals"])
        self.assertEqual(len(rows), 1, "the cancelled record stays visible as history")
        self.assertIsNotNone(cancelled.cancelled_at, "cancellation evidence survives")
        self.assertNotIn(str(DisposalMethodChoices.RECYCLE.label), chart_svg)
