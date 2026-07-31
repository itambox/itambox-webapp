"""B1/B2-class follow-up: SubscriptionForm.cost_center must be tenant-scoped."""

from django.test import TestCase
from model_bakery import baker

from core.tests.mixins import TenantTestMixin
from organization.models import CostCenter, Tenant
from subscriptions.forms import SubscriptionBulkEditForm, SubscriptionBulkImportForm, SubscriptionForm
from subscriptions.models import Subscription


class SubscriptionFormFkScopingTests(TenantTestMixin, TestCase):
    def test_form_exposes_canonical_terms_but_not_lifecycle_fields(self):
        fields = SubscriptionForm().fields
        self.assertIn("vendor_contract_auto_renews", fields)
        self.assertNotIn("auto_renewal", fields)
        self.assertNotIn("status", fields)
        self.assertNotIn("cancellation_date", fields)

        bulk_fields = SubscriptionBulkEditForm(model=Subscription).fields
        self.assertNotIn("status", bulk_fields)
        self.assertNotIn("cancellation_date", bulk_fields)

    def test_import_form_maps_legacy_and_canonical_renewal_terms_without_lifecycle_fields(self):
        form = SubscriptionBulkImportForm()
        self.assertNotIn("status", form.field_names)
        self.assertNotIn("cancellation_date", form.field_names)
        self.assertEqual(
            form.map_row({"auto_renewal": "false"})["vendor_contract_auto_renews"],
            False,
        )
        self.assertEqual(
            form.map_row({"vendor_contract_auto_renews": "yes"})["vendor_contract_auto_renews"],
            True,
        )
        with self.assertRaisesMessage(Exception, "conflicts"):
            form.map_row({"auto_renewal": "false", "vendor_contract_auto_renews": "true"})

    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="sffk-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="sffk-b")
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def test_cost_center_scoped_to_tenant(self):
        pks = set(SubscriptionForm().fields["cost_center"].queryset.values_list("pk", flat=True))
        self.assertIn(self.cc_a.pk, pks)
        self.assertNotIn(self.cc_b.pk, pks)
