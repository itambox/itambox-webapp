"""Warranty Expiration report — compiler tests.

Covers:
  - Row content from a real Warranty object with a non-USD currency.
  - Money cell is NOT '$'-prefixed when currency is EUR.
  - Summary cards: total, expiring-soon, expired counts correct.
  - Tenant scoping: a second-tenant warranty does not appear.
"""
import datetime

import pytest
from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, StatusLabel
from assets.models.lifecycle import Warranty
from assets.models.choices import WarrantyTypeChoices
from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
from organization.models import Tenant


class WarrantyExpirationReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='WarrantyTenant', slug='warranty-tenant')
        self.set_active_tenant(self.tenant)

        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(
            Asset,
            name='Dell XPS 15',
            asset_tag='AST-W-001',
            tenant=self.tenant,
            status=self.status,
        )

        today = datetime.date.today()
        # Active warranty with a future end_date, non-USD currency (EUR).
        self.warranty_active = Warranty.objects.create(
            asset=self.asset,
            warranty_type=WarrantyTypeChoices.HARDWARE,
            provider='Dell ProSupport',
            start_date=today - datetime.timedelta(days=365),
            end_date=today + datetime.timedelta(days=400),
            cost=199.00,
            currency='EUR',
            reference='REF-001',
        )
        # Expiring-soon warranty (within 30 days).
        self.warranty_expiring = Warranty.objects.create(
            asset=self.asset,
            warranty_type=WarrantyTypeChoices.EXTENDED,
            provider='ExtendedCo',
            start_date=today - datetime.timedelta(days=300),
            end_date=today + datetime.timedelta(days=15),
            cost=None,
            currency='EUR',
            reference='REF-002',
        )
        # Expired warranty (end_date in the past).
        self.warranty_expired = Warranty.objects.create(
            asset=self.asset,
            warranty_type=WarrantyTypeChoices.PARTS_LABOR,
            provider='OldVendor',
            start_date=today - datetime.timedelta(days=730),
            end_date=today - datetime.timedelta(days=10),
            cost=None,
            currency='EUR',
            reference='REF-003',
        )

        # A second tenant — its warranty must not leak into this report.
        self.tenant_b = Tenant.objects.create(name='OtherTenant', slug='other-tenant-w')
        self.asset_b = baker.make(
            Asset,
            name='HP EliteBook',
            asset_tag='AST-W-B-001',
            tenant=self.tenant_b,
            status=self.status,
        )
        Warranty.objects.create(
            asset=self.asset_b,
            warranty_type=WarrantyTypeChoices.FULL,
            provider='HP Care',
            start_date=today - datetime.timedelta(days=100),
            end_date=today + datetime.timedelta(days=200),
            cost=299.00,
            currency='USD',
            reference='REF-B-001',
        )

        self.template = ReportTemplate.objects.create(
            name='Warranty Expiration Test',
            report_type=ReportTemplate.REPORT_TYPE_WARRANTY_EXPIRATION,
            included_columns=[
                'warranty_asset', 'warranty_type', 'warranty_provider',
                'warranty_end_date', 'warranty_days_remaining', 'warranty_status',
                'warranty_cost', 'warranty_reference',
            ],
            include_summary_cards=True,
            include_distribution_chart=True,
        )

    def test_row_content_and_non_usd_money(self):
        """Active EUR warranty row is present and cost is not '$'-prefixed."""
        self.clear_tenant_context()
        _, rows, summary_cards, _, chart_svg, _ = compile_report_context(
            self.template, active_tenant=self.tenant
        )

        # Verify the active warranty row exists with correct fields.
        active_rows = [r for r in rows if r.get('Provider') == 'Dell ProSupport']
        self.assertEqual(len(active_rows), 1, "Expected exactly one row for Dell ProSupport warranty")
        active_row = active_rows[0]

        self.assertEqual(active_row['Asset'], 'Dell XPS 15')
        self.assertEqual(active_row['Warranty Type'], 'Hardware')
        self.assertEqual(active_row['Reference'], 'REF-001')
        self.assertEqual(active_row['Status'], 'Active')

        # Days remaining must be a positive integer string.
        days = int(active_row['Days Remaining'])
        self.assertGreater(days, 0)

        # EUR money cell must NOT start with '$'.
        cost_cell = active_row['Warranty Cost']
        self.assertFalse(
            cost_cell.startswith('$'),
            f"EUR warranty cost cell should not start with '$', got: {cost_cell!r}",
        )
        self.assertNotEqual(cost_cell, '-', "Expected a formatted cost, not '-'")

    def test_summary_cards_counts(self):
        """Summary cards correctly reflect total / expiring / expired counts."""
        self.clear_tenant_context()
        _, _, summary_cards, *_ = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        card_map = {c['label']: c['value'] for c in summary_cards}

        self.assertEqual(card_map['Total Warranties'], '3')
        self.assertEqual(card_map['Expiring Within 30 Days'], '1')
        self.assertEqual(card_map['Already Expired'], '1')

    def test_tenant_scoping_excludes_other_tenant(self):
        """Warranties belonging to a different tenant do not appear in the rows."""
        self.clear_tenant_context()
        _, rows, summary_cards, *_ = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        providers = [r.get('Provider') for r in rows]
        self.assertNotIn('HP Care', providers, "Other-tenant warranty must not leak into report")

        card_map = {c['label']: c['value'] for c in summary_cards}
        self.assertEqual(card_map['Total Warranties'], '3')

    def test_expiring_soon_row_status(self):
        """The expiring-soon warranty row has Status == 'Expiring Soon'."""
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        expiring_rows = [r for r in rows if r.get('Provider') == 'ExtendedCo']
        self.assertEqual(len(expiring_rows), 1)
        self.assertEqual(expiring_rows[0]['Status'], 'Expiring Soon')

    def test_expired_row_status_and_negative_days(self):
        """The expired warranty row has Status == 'Expired' and negative Days Remaining."""
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        expired_rows = [r for r in rows if r.get('Provider') == 'OldVendor']
        self.assertEqual(len(expired_rows), 1)
        self.assertEqual(expired_rows[0]['Status'], 'Expired')
        days = int(expired_rows[0]['Days Remaining'])
        self.assertLess(days, 0)

    def test_distribution_chart_generated(self):
        """include_distribution_chart=True produces a non-empty SVG string."""
        self.clear_tenant_context()
        _, _, _, _, chart_svg, _ = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        self.assertTrue(bool(chart_svg), "Expected a non-empty chart SVG")
