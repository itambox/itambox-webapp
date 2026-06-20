"""B4/B5 regression: report compiler tenant scoping & correct figures.

B4: License Utilization counted soft-deleted (checked-in) seats as assigned.
B5: Software Inventory ignored active_tenant/filter_tenants, leaking every
    tenant's catalogue (and installs/licence counts) into MSP/scheduled reports.

The tests clear the ambient tenant context before compiling (the scheduled /
MSP scenario) so it is the report's explicit tenant scoping that is exercised,
not the manager's ambient scope.
"""
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from organization.models import Tenant, AssetHolder
from extras.models import ReportTemplate
from software.models import Software, InstalledSoftware
from licenses.models import License, LicenseSeatAssignment
from assets.models import Asset, StatusLabel
from core.reports import compile_report_context
from core.tests.mixins import TenantTestMixin


class LicenseUtilizationReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Rep Tenant', slug='rep-tenant')
        self.set_active_tenant(self.tenant)
        self.mfr = baker.make('assets.Manufacturer')
        self.software = baker.make(Software, name='Acme', manufacturer=self.mfr, tenant=self.tenant)
        self.license = baker.make(
            License, name='Acme EA', software=self.software, seats=10, tenant=self.tenant
        )
        h1, h2, h3 = (baker.make(AssetHolder, tenant=self.tenant) for _ in range(3))
        # 2 active seats + 1 checked-in (soft-deleted) seat.
        LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h1)
        LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h2)
        gone = LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h3)
        LicenseSeatAssignment.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

        self.template = ReportTemplate.objects.create(
            name='Lic Util',
            report_type=ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION,
            included_columns=['license_name', 'seats', 'assigned_seats', 'available_seats'],
            include_summary_cards=True,
        )

    def test_assigned_seats_excludes_soft_deleted(self):
        self.clear_tenant_context()
        _, rows, *_ = compile_report_context(self.template, active_tenant=self.tenant)
        row = next(r for r in rows if r.get('License Name') == 'Acme EA')
        self.assertEqual(row['Assigned Seats'], '2')      # not 3
        self.assertEqual(row['Available Seats'], '8')      # 10 - 2


class SoftwareInventoryReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='soft-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='soft-b')
        self.mfr = baker.make('assets.Manufacturer')
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

        self.set_active_tenant(self.tenant)
        self.sw_a = baker.make(Software, name='SoftA', manufacturer=self.mfr, tenant=self.tenant)
        # One install of SoftA on a tenant-A asset (a cross-tenant install is
        # rejected by InstalledSoftware.clean, so the count is inherently
        # tenant-scoped; this just exercises the scoped count path).
        asset_a = baker.make(Asset, tenant=self.tenant, asset_tag='A-1', status=self.status)
        InstalledSoftware.objects.create(software=self.sw_a, asset=asset_a)

        self.sw_b = baker.make(Software, name='SoftB', manufacturer=self.mfr, tenant=self.tenant_b)

        self.template = ReportTemplate.objects.create(
            name='Soft Inv',
            report_type=ReportTemplate.REPORT_TYPE_SOFTWARE_INVENTORY,
            included_columns=['software_name', 'manufacturer', 'installed_count'],
            include_summary_cards=True,
        )

    def test_inventory_scoped_to_report_tenant(self):
        self.clear_tenant_context()
        _, rows, summary_cards, *_ = compile_report_context(self.template, active_tenant=self.tenant)
        names = [r.get('Software Product') for r in rows]
        self.assertIn('SoftA', names)
        self.assertNotIn('SoftB', names)

        total = next(c['value'] for c in summary_cards if c['label'] == 'Total Software Products')
        self.assertEqual(total, '1')

        row_a = next(r for r in rows if r.get('Software Product') == 'SoftA')
        self.assertEqual(row_a['Installed Count'], '1')
