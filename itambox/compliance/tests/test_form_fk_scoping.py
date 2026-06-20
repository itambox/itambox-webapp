"""B1/B2-class follow-up: AssetMaintenanceForm.asset must be tenant-scoped."""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant
from assets.models import Asset, StatusLabel
from compliance.forms import AssetMaintenanceForm
from core.tests.mixins import TenantTestMixin


class AssetMaintenanceFormScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='amfk-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='amfk-b')
        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_a = baker.make(Asset, tenant=self.tenant, asset_tag='AMFK-A', status=status)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b, asset_tag='AMFK-B', status=status)
        self.set_active_tenant(self.tenant)

    def test_asset_field_scoped_to_tenant(self):
        pks = set(AssetMaintenanceForm().fields['asset'].queryset.values_list('pk', flat=True))
        self.assertIn(self.asset_a.pk, pks)
        self.assertNotIn(self.asset_b.pk, pks)
