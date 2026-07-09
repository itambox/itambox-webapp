"""Test for the hardware_inventory report type."""
from django.test import TestCase
from django.utils.translation import gettext as _
from core.tests.mixins import TenantTestMixin


class HardwareInventoryReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='HW Inv Tenant', slug='hw-inv-tenant')

    def test_hardware_inventory_report(self):
        from assets.models import Manufacturer
        from inventory.models import Accessory
        from extras.models import ReportTemplate
        from core.reports import compile_report_context

        mfr = Manufacturer.objects.create(name='Dell HW', slug='dell-hw')
        Accessory.objects.create(name='USB-C Dock', manufacturer=mfr, tenant=self.tenant, min_qty=5)

        tpl = ReportTemplate.objects.create(
            name='HW Inventory', report_type=ReportTemplate.REPORT_TYPE_HARDWARE_INVENTORY,
            tenant=self.tenant,
        )
        headers, rows, cards, grouped, chart, ctx = compile_report_context(tpl, active_tenant=self.tenant)

        names = [r.get(_('Name')) for r in rows]
        self.assertIn('USB-C Dock', names)
        acc_card = next((c for c in cards if c['label'] == _('Accessory SKUs')), None)
        self.assertIsNotNone(acc_card)
        self.assertEqual(acc_card['value'], '1')

"""Tests for the Custody & EULA Sign-off Compliance report type."""
import pytest
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant, AssetHolder
from assets.models import Asset, StatusLabel
from compliance.models import CustodyReceipt
from extras.models import ReportTemplate
from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin


def _summary(cards, label):
    return next(c['value'] for c in cards if c['label'] == label)
