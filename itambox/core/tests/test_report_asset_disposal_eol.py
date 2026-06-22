"""Tests for the asset_disposal_eol report type.

Covers:
- Real row content (asset name, date, method, WEEE flag, proceeds).
- A non-USD proceeds cell does not start with '$'.
- Summary card values (total disposals, WEEE count, total proceeds).
- Tenant isolation: a second tenant's disposal is excluded.
"""
import pytest
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant
from extras.models import ReportTemplate
from assets.models import Asset, StatusLabel
from assets.models.lifecycle import AssetDisposal
from assets.models.choices import DisposalMethodChoices, DataSanitizationMethodChoices
from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin


class AssetDisposalEolReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Disposal Tenant', slug='disposal-tenant')
        self.tenant_b = Tenant.objects.create(name='Other Tenant', slug='disposal-other')

        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

        # Tenant-A asset with a EUR disposal (non-USD — proceeds must not start with '$')
        self.set_active_tenant(self.tenant)
        self.asset_a = baker.make(
            Asset,
            name='Lenovo ThinkPad X1',
            asset_tag='AST-D001',
            tenant=self.tenant,
            status=self.status,
        )
        self.disposal_a = AssetDisposal.objects.create(
            asset=self.asset_a,
            disposal_date='2026-05-15',
            disposal_method=DisposalMethodChoices.RECYCLE,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_PURGE,
            sanitization_certificate='CERT-2026-EUR',
            sanitized_by='SecureWipe GmbH',
            recipient='GreenIT Recyclers',
            proceeds=120,
            currency='EUR',
            weee_compliant=True,
        )

        # Tenant-A second disposal, no proceeds, not WEEE compliant
        self.asset_a2 = baker.make(
            Asset,
            name='Dell Latitude 5420',
            asset_tag='AST-D002',
            tenant=self.tenant,
            status=self.status,
        )
        self.disposal_a2 = AssetDisposal.objects.create(
            asset=self.asset_a2,
            disposal_date='2026-06-01',
            disposal_method=DisposalMethodChoices.DESTRUCTION,
            data_sanitization_method=DataSanitizationMethodChoices.NIST_DESTROY,
            proceeds=None,
            currency='',
            weee_compliant=False,
        )

        # Tenant-B disposal — must NOT appear in tenant-A report
        self.set_active_tenant(self.tenant_b)
        self.asset_b = baker.make(
            Asset,
            name='Apple MacBook Air',
            asset_tag='AST-D003',
            tenant=self.tenant_b,
            status=self.status,
        )
        self.disposal_b = AssetDisposal.objects.create(
            asset=self.asset_b,
            disposal_date='2026-06-10',
            disposal_method=DisposalMethodChoices.DONATION,
            data_sanitization_method=DataSanitizationMethodChoices.NONE,
            proceeds=None,
            currency='',
            weee_compliant=False,
        )

        self.template = ReportTemplate.objects.create(
            name='EOL Disposal Report',
            report_type=ReportTemplate.REPORT_TYPE_ASSET_DISPOSAL_EOL,
            included_columns=[
                'disposal_asset', 'disposal_date', 'disposal_method',
                'disposal_sanitization_method', 'disposal_sanitization_certificate',
                'disposal_sanitized_by', 'disposal_recipient',
                'disposal_proceeds', 'disposal_weee_compliant',
            ],
            include_summary_cards=True,
            include_distribution_chart=True,
        )

    def test_row_content_correct(self):
        """Real disposal rows contain expected field values."""
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(self.template, active_tenant=self.tenant)

        # Asset.__str__ returns "name (asset_tag)" — use substring match.
        asset_cells = [r.get('Asset') or '' for r in rows]
        self.assertTrue(
            any('Lenovo ThinkPad X1' in cell for cell in asset_cells),
            f"Expected 'Lenovo ThinkPad X1' in Asset cells, got: {asset_cells}",
        )
        self.assertTrue(
            any('Dell Latitude 5420' in cell for cell in asset_cells),
            f"Expected 'Dell Latitude 5420' in Asset cells, got: {asset_cells}",
        )

        row_a = next(r for r in rows if 'Lenovo ThinkPad X1' in (r.get('Asset') or ''))
        self.assertEqual(row_a['Disposal Date'], '2026-05-15')
        self.assertEqual(row_a['Disposal Method'], 'Recycle / WEEE')
        self.assertEqual(row_a['Data Sanitization Method'], 'NIST Purge (cryptographic or ATA Secure Erase)')
        self.assertEqual(row_a['Sanitization Certificate'], 'CERT-2026-EUR')
        self.assertEqual(row_a['Sanitized By'], 'SecureWipe GmbH')
        self.assertEqual(row_a['Recipient'], 'GreenIT Recyclers')
        self.assertEqual(row_a['WEEE Compliant'], 'Yes')

        row_a2 = next(r for r in rows if 'Dell Latitude 5420' in (r.get('Asset') or ''))
        self.assertEqual(row_a2['WEEE Compliant'], 'No')
        self.assertEqual(row_a2['Proceeds'], '-')

    def test_proceeds_non_usd_not_dollar_prefixed(self):
        """EUR proceeds cell must not start with '$'."""
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(self.template, active_tenant=self.tenant)
        row_a = next(r for r in rows if 'Lenovo ThinkPad X1' in (r.get('Asset') or ''))
        proceeds_cell = row_a.get('Proceeds', '')
        self.assertNotEqual(proceeds_cell, '-', 'Proceeds should be rendered, not dash')
        self.assertFalse(
            proceeds_cell.startswith('$'),
            f"EUR proceeds must not start with '$', got: {proceeds_cell!r}",
        )
        # EUR symbol should appear in the rendered value
        self.assertIn('€', proceeds_cell)

    def test_tenant_isolation(self):
        """Tenant-B disposal must not appear in a tenant-A report."""
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(self.template, active_tenant=self.tenant)
        asset_cells = [r.get('Asset') or '' for r in rows]
        self.assertFalse(
            any('Apple MacBook Air' in cell for cell in asset_cells),
            f"Tenant-B asset 'Apple MacBook Air' must not appear in tenant-A report, got: {asset_cells}",
        )

    def test_summary_cards_correct(self):
        """Summary cards show correct total, WEEE count, and proceeds."""
        self.clear_tenant_context()
        _, _rows, summary_cards, *_ = compile_report_context(self.template, active_tenant=self.tenant)

        card_labels = [c['label'] for c in summary_cards]
        self.assertIn('Total Disposals', card_labels)
        self.assertIn('WEEE Compliant', card_labels)
        self.assertIn('Total Proceeds', card_labels)

        total_card = next(c for c in summary_cards if c['label'] == 'Total Disposals')
        self.assertEqual(total_card['value'], '2')

        weee_card = next(c for c in summary_cards if c['label'] == 'WEEE Compliant')
        self.assertEqual(weee_card['value'], '1')

        proceeds_card = next(c for c in summary_cards if c['label'] == 'Total Proceeds')
        # Should contain EUR symbol, not '$'
        self.assertIn('€', proceeds_card['value'])
        self.assertFalse(proceeds_card['value'].startswith('$'))

    def test_distribution_chart_rendered(self):
        """include_distribution_chart=True produces a non-empty SVG string."""
        self.clear_tenant_context()
        _headers, _rows, _cards, _grouped, chart_svg, _ctx = compile_report_context(
            self.template, active_tenant=self.tenant
        )
        self.assertTrue(chart_svg, 'chart_svg should be non-empty when include_distribution_chart=True')
        self.assertIn('<svg', chart_svg)

    def test_filter_tenants_scoping(self):
        """filter_tenants=[tenant_b] excludes tenant-A disposals."""
        self.clear_tenant_context()
        _, rows, summary_cards, *_ = compile_report_context(
            self.template, filter_tenants=[self.tenant_b]
        )
        asset_cells = [r.get('Asset') or '' for r in rows]
        self.assertTrue(
            any('Apple MacBook Air' in cell for cell in asset_cells),
            f"Expected 'Apple MacBook Air' in tenant-B scoped report, got: {asset_cells}",
        )
        self.assertFalse(
            any('Lenovo ThinkPad X1' in cell for cell in asset_cells),
            f"Tenant-A asset 'Lenovo ThinkPad X1' must not appear in tenant-B report, got: {asset_cells}",
        )
        total_card = next(c for c in summary_cards if c['label'] == 'Total Disposals')
        self.assertEqual(total_card['value'], '1')
