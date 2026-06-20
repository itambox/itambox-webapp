"""B1 regression: asset disposal must be scoped to the active tenant.

The AssetDisposalForm `asset` choice field was an import-frozen, unscoped
queryset, so a Tenant A user could submit Tenant B's asset pk and destructively
dispose it. The form now rescopes the field per request, and the dispose action
view re-fetches the asset through the tenant-scoped manager before disposing.
"""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant
from assets.models import Asset, StatusLabel
from assets.forms.disposal_form import AssetDisposalForm
from core.tests.mixins import TenantTestMixin


class DisposalTenantIsolationTests(TenantTestMixin, TestCase):
    def setUp(self):
        # Tenant A is the active tenant; Tenant B is the victim.
        self.setup_tenant_context(name='Tenant A', slug='tenant-a')
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_a = baker.make(Asset, tenant=self.tenant, asset_tag='A-1', status=self.status)

        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')
        self.asset_b = baker.make(Asset, tenant=self.tenant_b, asset_tag='B-1', status=self.status)

    def test_form_asset_queryset_excludes_other_tenant(self):
        self.set_active_tenant(self.tenant)
        form = AssetDisposalForm()
        ids = set(form.fields['asset'].queryset.values_list('pk', flat=True))
        self.assertIn(self.asset_a.pk, ids)
        self.assertNotIn(self.asset_b.pk, ids)

    def test_form_rejects_cross_tenant_asset(self):
        self.set_active_tenant(self.tenant)
        form = AssetDisposalForm(data={
            'asset': self.asset_b.pk,
            'disposal_method': 'recycle',
            'disposal_date': '2026-06-20',
            'data_sanitization_method': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('asset', form.errors)

    def test_form_accepts_own_tenant_asset(self):
        self.set_active_tenant(self.tenant)
        form = AssetDisposalForm(data={
            'asset': self.asset_a.pk,
            'disposal_method': 'recycle',
            'disposal_date': '2026-06-20',
            'data_sanitization_method': 'none',
        })
        # The asset field at least must validate to its own-tenant choice.
        self.assertNotIn('asset', form.errors)
