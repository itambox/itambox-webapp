"""#496 repair10: the disposal report must distinguish cancelled from active rows.

The evidence rows keep every record (cancelled ones included), but a row may never be
indistinguishable from an active disposal: the default report renders the disposal status,
the cancellation metadata is available through the published column vocabulary, and the
effective cards/chart stay scoped to the authorised tenant and to uncancelled records.
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

CANCELLATION_REASON = "Recorded against the wrong asset"


class DisposalReportStatusVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Status Tenant", slug="status-tenant")
        self.other_tenant = Tenant.objects.create(name="Status Other", slug="status-other")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)
        self.set_active_tenant(self.tenant)
        self.template = baker.make(
            ReportTemplate,
            report_type="asset_disposal_eol",
            include_summary_cards=True,
            include_distribution_chart=True,
            tenant=self.tenant,
        )

    def _disposal(self, tag, method, proceeds, weee, tenant=None, cancelled=False):
        tenant = tenant or self.tenant
        asset = baker.make(Asset, name=f"Status asset {tag}", asset_tag=tag, tenant=tenant, status=self.status)
        record = AssetDisposal.objects.create(
            asset=asset,
            disposal_date="2026-05-15",
            disposal_method=method,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_PURGE,
            proceeds=proceeds,
            currency="EUR",
            weee_compliant=weee,
        )
        if cancelled:
            cancel_asset_disposal(record, user=self.tenant_user, reason=CANCELLATION_REASON)
            record.refresh_from_db()
        return record

    def _row_for(self, rows, tag):
        for row in rows:
            if tag in (row.get("Asset") or ""):
                return row
        raise AssertionError(f"no report row for {tag}: {[row.get('Asset') for row in rows]}")

    def _context(self, template=None):
        _headers, rows, cards, _grouped, chart_svg, _context = build_report_context(
            template or self.template, active_tenant=self.tenant
        )
        return rows, {card["label"]: card["value"] for card in cards}, chart_svg

    def test_default_report_rows_distinguish_cancelled_from_active(self):
        self._disposal("RPT-ACTIVE", DisposalMethodChoices.DONATION, Decimal("100.00"), True)
        self._disposal("RPT-CANCELLED", DisposalMethodChoices.RECYCLE, Decimal("999.00"), True, cancelled=True)

        rows, cards, _chart = self._context()

        self.assertEqual(self._row_for(rows, "RPT-ACTIVE")["Disposal Status"], "Disposed")
        self.assertEqual(self._row_for(rows, "RPT-CANCELLED")["Disposal Status"], "Cancelled")
        # The same rows feed the effective cards: only the active record counts.
        self.assertEqual(cards["Active Disposals"], "1")
        self.assertNotIn("999", cards["Proceeds from active disposals"])

    def test_cancellation_metadata_is_selectable_through_the_vocabulary(self):
        active = self._disposal("RPT-SEL-A", DisposalMethodChoices.DONATION, Decimal("10.00"), True)
        cancelled = self._disposal("RPT-SEL-C", DisposalMethodChoices.RECYCLE, Decimal("20.00"), False, cancelled=True)
        template = baker.make(
            ReportTemplate,
            report_type="asset_disposal_eol",
            include_summary_cards=False,
            include_distribution_chart=False,
            tenant=self.tenant,
            included_columns=[
                "disposal_asset",
                "disposal_status",
                "disposal_cancelled_at",
                "disposal_cancelled_by",
                "disposal_cancellation_reason",
            ],
        )

        rows, _cards, _chart = self._context(template)

        cancelled_row = self._row_for(rows, "RPT-SEL-C")
        self.assertEqual(cancelled_row["Disposal Status"], "Cancelled")
        self.assertEqual(cancelled_row["Cancelled At"], cancelled.cancelled_at.strftime("%Y-%m-%d"))
        self.assertEqual(cancelled_row["Cancelled By"], str(self.tenant_user))
        self.assertEqual(cancelled_row["Disposal cancellation reason"], CANCELLATION_REASON)
        # An active record carries no cancellation metadata, and stays distinguishable.
        active_row = self._row_for(rows, "RPT-SEL-A")
        self.assertEqual(active_row["Disposal Status"], "Disposed")
        self.assertEqual(active_row["Cancelled At"], "-")
        self.assertEqual(active_row["Cancelled By"], "-")
        self.assertEqual(active_row["Disposal cancellation reason"], "-")
        self.assertIsNotNone(cancelled.cancelled_at)
        self.assertIsNone(active.cancelled_at)

    def test_rows_cards_and_chart_stay_inside_the_authorised_tenant(self):
        self._disposal("RPT-MINE", DisposalMethodChoices.DONATION, Decimal("100.00"), True)
        with self.tenant_context(self.other_tenant):
            self._disposal(
                "RPT-FOREIGN", DisposalMethodChoices.RECYCLE, Decimal("888.00"), True, tenant=self.other_tenant
            )

        rows, cards, chart_svg = self._context()

        tags = [row["Asset"] for row in rows]
        self.assertTrue(any("RPT-MINE" in tag for tag in tags), tags)
        self.assertFalse(any("RPT-FOREIGN" in tag for tag in tags), tags)
        self.assertEqual(cards["Active Disposals"], "1")
        self.assertNotIn("888", cards["Proceeds from active disposals"])
        self.assertNotIn(str(DisposalMethodChoices.RECYCLE.label), chart_svg)
