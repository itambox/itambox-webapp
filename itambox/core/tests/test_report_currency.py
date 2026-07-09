import datetime
from decimal import Decimal
from django.test import TestCase
from model_bakery import baker
from organization.models import Tenant
from extras.models import ReportTemplate
from assets.models import Asset, AssetMaintenance, StatusLabel
from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin


def _summary(cards, label):
    return next(c['value'] for c in cards if c['label'] == label)


class MixedCurrencyReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Curr Tenant', slug='curr-tenant')
        Tenant.objects.filter(pk=self.tenant.pk).update(currency='EUR')
        self.tenant.refresh_from_db()
        self.set_active_tenant(self.tenant)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.purchase_date = datetime.date(2026, 1, 15)
        self.asset_eur = baker.make(Asset, tenant=self.tenant, asset_tag='EUR-1', status=self.status, purchase_cost=Decimal('1000.00'), currency='EUR', purchase_date=self.purchase_date)
        self.asset_usd = baker.make(Asset, tenant=self.tenant, asset_tag='USD-1', status=self.status, purchase_cost=Decimal('2000.00'), currency='USD', purchase_date=self.purchase_date)

    def _compile(self, report_type, columns):
        template = ReportTemplate.objects.create(name=f'{report_type} report', report_type=report_type, included_columns=columns, include_summary_cards=True)
        self.clear_tenant_context()
        return compile_report_context(template, active_tenant=self.tenant)

    def _assert_per_currency(self, value):
        self.assertIn('$', value)
        self.assertIn('€', value)
        self.assertIn('·', value)
        self.assertNotIn('3,000', value)
        self.assertNotIn('3.000', value)

    def test_asset_summary_acquisition_sum_per_currency(self):
        _, _rows, cards, *_ = self._compile(ReportTemplate.REPORT_TYPE_ASSET_SUMMARY, ['asset_tag', 'purchase_cost'])
        self._assert_per_currency(_summary(cards, 'Total Acquisition Sum'))

    def test_asset_depreciation_book_value_per_currency(self):
        _, _rows, cards, *_ = self._compile(ReportTemplate.REPORT_TYPE_ASSET_DEPRECIATION, ['asset_tag', 'purchase_cost', 'current_value'])
        self._assert_per_currency(_summary(cards, 'Total Acquisition Cost'))
        self._assert_per_currency(_summary(cards, 'Total Current Book Value'))

    def test_asset_maintenance_cost_per_currency(self):
        AssetMaintenance.objects.create(asset=self.asset_eur, cost=Decimal('100.00'), currency='EUR', start_date=self.asset_eur.purchase_date)
        AssetMaintenance.objects.create(asset=self.asset_usd, cost=Decimal('300.00'), currency='USD', start_date=self.asset_usd.purchase_date)
        _, _rows, cards, *_ = self._compile(ReportTemplate.REPORT_TYPE_ASSET_MAINTENANCE, ['maintenance_asset', 'maintenance_cost'])
        value = _summary(cards, 'Total Maintenance Cost')
        self.assertIn('$', value)
        self.assertIn('€', value)
        self.assertIn('·', value)
        self.assertNotIn('400', value)
