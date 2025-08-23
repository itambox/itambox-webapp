from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Asset, AssetRole, StatusLabel
from organization.models import AssetHolder
from .models import AssetMaintenance, CustodyReceipt

User = get_user_model()


class AssetMaintenanceModelTests(TestCase):
    def setUp(self):
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.status = StatusLabel.objects.create(
            name='Deployable', slug='deployable', type='deployable', color='00ff00'
        )
        self.asset = Asset.objects.create(
            name='SRV-01', asset_tag='TAG-SRV-01', asset_role=self.role, status=self.status
        )

    def test_maintenance_creation(self):
        maint = AssetMaintenance.objects.create(
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            supplier='Dell Support',
            cost=150.00,
            start_date=date(2026, 1, 15),
            completion_date=date(2026, 1, 18),
            notes='Replaced power supply',
        )
        self.assertEqual(str(maint), 'Repair on SRV-01')
        self.assertEqual(maint.cost, 150.00)
        self.assertEqual(maint.supplier, 'Dell Support')

    def test_maintenance_downtime_days(self):
        maint = AssetMaintenance.objects.create(
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_UPGRADE,
            start_date=date(2026, 3, 1),
            completion_date=date(2026, 3, 10),
        )
        self.assertEqual(maint.downtime_days, 9)

    def test_maintenance_downtime_none_when_incomplete(self):
        maint = AssetMaintenance.objects.create(
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_CALIBRATION,
            start_date=date(2026, 4, 1),
        )
        self.assertIsNone(maint.downtime_days)

    def test_maintenance_absolute_url(self):
        maint = AssetMaintenance.objects.create(
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
        maint = AssetMaintenance.objects.create(
            asset=self.asset,
            start_date=date(2026, 1, 1),
        )
        self.assertEqual(maint.maintenance_type, AssetMaintenance.MAINTENANCE_TYPE_REPAIR)

    def test_maintenance_ordering(self):
        AssetMaintenance.objects.create(
            asset=self.asset, start_date=date(2026, 1, 1), maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR
        )
        AssetMaintenance.objects.create(
            asset=self.asset, start_date=date(2026, 6, 1), maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_UPGRADE
        )
        qs = AssetMaintenance.objects.all()
        self.assertGreater(qs[0].start_date, qs[1].start_date)


class CustodyReceiptModelTests(TestCase):
    def setUp(self):
        self.role = AssetRole.objects.create(name='Laptop', slug='laptop')
        self.status = StatusLabel.objects.create(
            name='Deployable', slug='deployable', type='deployable', color='00ff00'
        )
        self.asset = Asset.objects.create(
            name='LT-01', asset_tag='TAG-LT-01', asset_role=self.role, status=self.status
        )
        self.holder = AssetHolder.objects.create(
            first_name='Jane', last_name='Smith', upn='jane.smith', email='jane@test.com'
        )

    def test_custody_receipt_creation(self):
        receipt = CustodyReceipt.objects.create(
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
        r1 = CustodyReceipt.objects.create(asset=self.asset, holder=self.holder)
        r2 = CustodyReceipt.objects.create(asset=self.asset, holder=self.holder)
        self.assertNotEqual(r1.token, r2.token)

    def test_custody_receipt_string(self):
        receipt = CustodyReceipt.objects.create(asset=self.asset, holder=self.holder)
        self.assertIn('LT-01', str(receipt))
        self.assertIn('Jane Smith', str(receipt))


class AssetMaintenanceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.status = StatusLabel.objects.create(
            name='Deployable', slug='deployable', type='deployable', color='00ff00'
        )
        self.asset = Asset.objects.create(
            name='SRV-01', asset_tag='TAG-SRV-01', asset_role=self.role, status=self.status
        )
        self.maintenance = AssetMaintenance.objects.create(
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            supplier='Dell',
            cost=250.00,
            start_date=date(2026, 1, 1),
            completion_date=date(2026, 1, 5),
            notes='Replaced motherboard',
        )

    def test_list_view(self):
        url = reverse('assets:assetmaintenance_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SRV-01')

    def test_detail_view(self):
        url = reverse('assets:assetmaintenance_detail', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SRV-01')
        self.assertContains(response, '250.00')

    def test_create_view_get(self):
        url = reverse('assets:assetmaintenance_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('assets:assetmaintenance_create')
        response = self.client.post(url, {
            'asset': self.asset.pk,
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_UPGRADE,
            'supplier': 'HP Support',
            'cost': '500.00',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-03',
            'notes': 'RAM upgrade to 64GB',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssetMaintenance.objects.filter(supplier='HP Support').exists())

    def test_edit_view_get(self):
        url = reverse('assets:assetmaintenance_update', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse('assets:assetmaintenance_update', kwargs={'pk': self.maintenance.pk})
        response = self.client.post(url, {
            'asset': self.asset.pk,
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            'supplier': 'Dell Premium',
            'cost': '300.00',
            'start_date': '2026-01-01',
            'completion_date': '2026-01-05',
            'notes': 'Replaced motherboard + CPU',
        })
        self.assertEqual(response.status_code, 302)
        self.maintenance.refresh_from_db()
        self.assertEqual(self.maintenance.supplier, 'Dell Premium')
        self.assertEqual(self.maintenance.cost, 300.00)

    def test_delete_view_get(self):
        url = reverse('assets:assetmaintenance_delete', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('assets:assetmaintenance_delete', kwargs={'pk': self.maintenance.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssetMaintenance.objects.filter(pk=self.maintenance.pk).exists())
