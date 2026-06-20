"""B1/B2-class follow-up: SubscriptionForm.cost_center must be tenant-scoped."""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant, CostCenter
from subscriptions.forms import SubscriptionForm
from core.tests.mixins import TenantTestMixin


class SubscriptionFormFkScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='sffk-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='sffk-b')
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def test_cost_center_scoped_to_tenant(self):
        pks = set(SubscriptionForm().fields['cost_center'].queryset.values_list('pk', flat=True))
        self.assertIn(self.cc_a.pk, pks)
        self.assertNotIn(self.cc_b.pk, pks)
