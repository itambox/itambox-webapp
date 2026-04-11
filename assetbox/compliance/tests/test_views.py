from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from model_bakery import baker
from assets.models import Asset, Supplier
from organization.models import AssetHolder
from ..models import AssetMaintenance, CustodyReceipt

User = get_user_model()

class AssetMaintenanceViewTests(TestCase):
    def setUp(self):
        self.user = baker.make(User, is_staff=True, is_superuser=True)
        # Set plain text password for login
        self.user.set_password('testpassword')
        self.user.save()
        self.client.login(username=self.user.username, password='testpassword')

        self.asset = baker.make(Asset, name='SRV-01', asset_tag='TAG-SRV-01', tenant=None)
        self.supplier = baker.make(Supplier, name='Dell', slug='dell')
        self.maintenance = baker.make(
            AssetMaintenance,
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
        hp_supplier = baker.make(Supplier, name='HP Support', slug='hp-support')
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
        dell_premium = baker.make(Supplier, name='Dell Premium', slug='dell-premium')
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
        self.asset = baker.make(Asset, name='LT-02', asset_tag='TAG-LT-02', tenant=None)
        self.holder = baker.make(AssetHolder, first_name='Evelyn', last_name='Carter', email='evelyn@test.com')
        self.receipt = baker.make(
            CustodyReceipt,
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
