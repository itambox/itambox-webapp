from datetime import date
from django.test import TestCase
from model_bakery import baker
from assets.models import Asset, Supplier
from organization.models import AssetHolder
from assets.models import AssetMaintenance
from ..models import CustodyReceipt

class AssetMaintenanceModelTests(TestCase):
    def setUp(self):
        # Baker automatically creates AssetRole and StatusLabel foreign keys
        self.asset = baker.make(Asset, name='SRV-01', asset_tag='TAG-SRV-01', tenant=None)
        self.supplier = baker.make(Supplier, name='Dell Support', slug='dell-support')

    def test_maintenance_creation(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            supplier=self.supplier,
            cost=150.00,
            start_date=date(2026, 1, 15),
            completion_date=date(2026, 1, 18),
            notes='Replaced power supply',
        )
        self.assertEqual(str(maint), 'Repair on SRV-01')
        self.assertEqual(maint.cost, 150.00)
        self.assertEqual(maint.supplier, self.supplier)

    def test_maintenance_downtime_days(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_UPGRADE,
            start_date=date(2026, 3, 1),
            completion_date=date(2026, 3, 10),
        )
        self.assertEqual(maint.downtime_days, 9)

    def test_maintenance_downtime_none_when_incomplete(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_CALIBRATION,
            start_date=date(2026, 4, 1),
            completion_date=None,
        )
        self.assertIsNone(maint.downtime_days)

    def test_maintenance_absolute_url(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            start_date=date(2026, 1, 1),
        )
        url = maint.get_absolute_url()
        self.assertIn(str(maint.pk), url)

    def test_maintenance_types(self):
        choices = dict(AssetMaintenance.MAINTENANCE_TYPE_CHOICES)
        self.assertIn('repair', choices)
        self.assertIn('upgrade', choices)
        self.assertIn('calibration', choices)
        self.assertIn('software_support', choices)
        self.assertIn('hardware_support', choices)

    def test_maintenance_default_type(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            start_date=date(2026, 1, 1),
        )
        # Note: baker.make respects field defaults, but if it generates a random value
        # we can explicitly let it be created or pass it. Let's make sure it holds the default choice.
        # Wait, if we want to test default Choice, we should instantiate via standard create or let baker handle it.
        # Actually, in compliance model, default maintenance_type is 'repair'. Let's verify that.
        self.assertEqual(maint.maintenance_type, AssetMaintenance.MAINTENANCE_TYPE_REPAIR)

    def test_maintenance_ordering(self):
        baker.make(
            AssetMaintenance, asset=self.asset, start_date=date(2026, 1, 1), maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR
        )
        baker.make(
            AssetMaintenance, asset=self.asset, start_date=date(2026, 6, 1), maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_UPGRADE
        )
        qs = AssetMaintenance.objects.all()
        self.assertGreater(qs[0].start_date, qs[1].start_date)

    def test_maintenance_soft_delete(self):
        maint = baker.make(
            AssetMaintenance,
            asset=self.asset,
            start_date=date(2026, 1, 1),
        )
        maint_pk = maint.pk
        maint.delete()
        self.assertIsNotNone(maint.deleted_at)
        self.assertFalse(AssetMaintenance.objects.filter(pk=maint_pk).exists())
        self.assertTrue(AssetMaintenance.all_objects.filter(pk=maint_pk).exists())


class CustodyReceiptModelTests(TestCase):
    def setUp(self):
        self.asset = baker.make(Asset, name='LT-01', asset_tag='TAG-LT-01', tenant=None)
        self.holder = baker.make(AssetHolder, first_name='Jane', last_name='Smith')

    def test_custody_receipt_creation(self):
        receipt = baker.make(
            CustodyReceipt,
            asset=self.asset,
            holder=self.holder,
        )
        self.assertEqual(receipt.asset, self.asset)
        self.assertEqual(receipt.holder, self.holder)
        self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_PENDING)
        self.assertFalse(receipt.accepted)
        self.assertIsNotNone(receipt.token)
        self.assertEqual(len(receipt.token), 64)

    def test_custody_receipt_token_is_unique(self):
        r1 = baker.make(CustodyReceipt, asset=self.asset, holder=self.holder)
        r2 = baker.make(CustodyReceipt, asset=self.asset, holder=self.holder)
        self.assertNotEqual(r1.token, r2.token)

    def test_custody_receipt_string(self):
        receipt = baker.make(CustodyReceipt, asset=self.asset, holder=self.holder)
        self.assertIn('LT-01', str(receipt))
        self.assertIn('Jane Smith', str(receipt))
