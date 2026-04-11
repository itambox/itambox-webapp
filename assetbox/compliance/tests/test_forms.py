from django.test import TestCase
from assets.models import Asset, AssetRole, StatusLabel
from compliance.forms import AssetMaintenanceForm

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
        # start_date is required
        form = AssetMaintenanceForm(data={
            'asset': self.asset.pk,
            'title': 'Scheduled maintenance',
            'maintenance_type': 'repair',
            'status': 'scheduled',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('start_date', form.errors)
