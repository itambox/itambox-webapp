from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from assets.models import Asset, AssetRole, StatusLabel, Supplier
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
        self.supplier = Supplier.objects.create(name='Dell Support', slug='dell-support')

    def test_maintenance_creation(self):
        maint = AssetMaintenance.objects.create(
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

    def test_maintenance_soft_delete(self):
        maint = AssetMaintenance.objects.create(
            asset=self.asset,
            start_date=date(2026, 1, 1),
        )
        maint_pk = maint.pk
        maint.delete()
        self.assertIsNotNone(maint.deleted_at)
        self.assertFalse(AssetMaintenance.objects.filter(pk=maint_pk).exists())
        self.assertTrue(AssetMaintenance.all_objects.filter(pk=maint_pk).exists())


class AssetMaintenanceFormTests(TestCase):
    def setUp(self):
        self.role = AssetRole.objects.create(name='Server', slug='server')
        self.status = StatusLabel.objects.create(
            name='Deployable', slug='deployable', type='deployable', color='00ff00'
        )
        self.asset = Asset.objects.create(
            name='SRV-01', asset_tag='TAG-SRV-01', asset_role=self.role, status=self.status
        )

    def test_form_validation_success(self):
        from compliance.forms import AssetMaintenanceForm
        form = AssetMaintenanceForm(data={
            'asset': self.asset.pk,
            'title': 'Scheduled maintenance',
            'maintenance_type': 'repair',
            'status': 'scheduled',
            'start_date': '2026-06-01',
            'cost': '125.50',
        })
        self.assertTrue(form.is_valid())

    def test_form_validation_missing_required(self):
        from compliance.forms import AssetMaintenanceForm
        # start_date is required
        form = AssetMaintenanceForm(data={
            'asset': self.asset.pk,
            'title': 'Scheduled maintenance',
            'maintenance_type': 'repair',
            'status': 'scheduled',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('start_date', form.errors)


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
        self.supplier = Supplier.objects.create(name='Dell', slug='dell')
        self.maintenance = AssetMaintenance.objects.create(
            asset=self.asset,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            supplier=self.supplier,
            cost=250.00,
            start_date=date(2026, 1, 1),
            completion_date=date(2026, 1, 5),
            notes='Replaced motherboard',
        )

    def test_list_view(self):
        url = reverse('compliance:assetmaintenance_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SRV-01')

    def test_detail_view(self):
        url = reverse('compliance:assetmaintenance_detail', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SRV-01')
        self.assertContains(response, '250.00')

    def test_create_view_get(self):
        url = reverse('compliance:assetmaintenance_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        hp_supplier = Supplier.objects.create(name='HP Support', slug='hp-support')
        url = reverse('compliance:assetmaintenance_create')
        response = self.client.post(url, {
            'asset': self.asset.pk,
            'title': 'RAM upgrade to 64GB',
            'status': 'scheduled',
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_UPGRADE,
            'supplier': hp_supplier.pk,
            'cost': '500.00',
            'start_date': '2026-06-01',
            'completion_date': '2026-06-03',
            'notes': 'RAM upgrade to 64GB',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssetMaintenance.objects.filter(supplier=hp_supplier).exists())

    def test_edit_view_get(self):
        url = reverse('compliance:assetmaintenance_update', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        dell_premium = Supplier.objects.create(name='Dell Premium', slug='dell-premium')
        url = reverse('compliance:assetmaintenance_update', kwargs={'pk': self.maintenance.pk})
        response = self.client.post(url, {
            'asset': self.asset.pk,
            'title': 'Replaced motherboard + CPU',
            'status': 'completed',
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            'supplier': dell_premium.pk,
            'cost': '300.00',
            'start_date': '2026-01-01',
            'completion_date': '2026-01-05',
            'notes': 'Replaced motherboard + CPU',
        })
        self.assertEqual(response.status_code, 302)
        self.maintenance.refresh_from_db()
        self.assertEqual(self.maintenance.supplier, dell_premium)
        self.assertEqual(self.maintenance.cost, 300.00)

    def test_delete_view_get(self):
        url = reverse('compliance:assetmaintenance_delete', kwargs={'pk': self.maintenance.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('compliance:assetmaintenance_delete', kwargs={'pk': self.maintenance.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssetMaintenance.objects.filter(pk=self.maintenance.pk).exists())


class CustodyReceiptViewTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.role = AssetRole.objects.create(name='Laptop', slug='laptop')
        self.status = StatusLabel.objects.create(
            name='Deployable', slug='deployable', type='deployable', color='00ff00'
        )
        self.asset = Asset.objects.create(
            name='LT-02', asset_tag='TAG-LT-02', asset_role=self.role, status=self.status
        )
        self.holder = AssetHolder.objects.create(
            first_name='Evelyn', last_name='Carter', upn='evelyn.carter', email='evelyn@test.com'
        )
        self.receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
        )

    def test_sign_portal_get(self):
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LT-02')
        self.assertContains(response, 'Evelyn Carter')

    def test_sign_portal_post_empty_signature(self):
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.post(url, {
            'action': 'accept',
            'signature_canvas': 'empty'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please provide a valid signature.')
        self.receipt.refresh_from_db()
        self.assertFalse(self.receipt.accepted)

    def test_sign_portal_post_decline(self):
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.post(url, {
            'action': 'decline'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You have declined the custody transfer.')
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.acceptance_status, CustodyReceipt.STATUS_DECLINED)
        self.assertFalse(self.receipt.accepted)

    def test_sign_portal_post_success(self):
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        sig_data = 'data:image/png;base64,iVBORw0KGgoAAAANS...'
        response = self.client.post(url, {
            'action': 'accept',
            'signature_canvas': sig_data
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'receipt')  # checks success page rendered
        self.receipt.refresh_from_db()
        self.assertTrue(self.receipt.accepted)
        self.assertEqual(self.receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
        self.assertEqual(self.receipt.signature_data, sig_data)
        self.assertIsNotNone(self.receipt.signature_hash)

    def test_sign_portal_already_accepted(self):
        self.receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
        self.receipt.save()
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'success')  # checks success page rendered

    def test_sign_portal_already_declined(self):
        self.receipt.acceptance_status = CustodyReceipt.STATUS_DECLINED
        self.receipt.save()
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'declined')

    def test_sign_portal_expired_link(self):
        from django.utils import timezone
        # set created_date to older than 7 days using update (auto_now_add is immutable on direct save)
        CustodyReceipt.objects.filter(pk=self.receipt.pk).update(created_date=timezone.now() - timedelta(days=8))
        url = reverse('compliance:custody_eula_sign', kwargs={'token': self.receipt.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'expired')

