"""Issue #500: commercial vocabulary on the subscriptions surfaces.

Pins the Provider-to-Supplier bridge (model, form, and its global reference
data contract), the exclusive Contract/Subscription ownership help, and the
agreement-entitlement seat vocabulary. Scope shapes for the Provider form FK
live in ``test_form_fk_scoping.py``.
"""

from django.test import TestCase

from assets.models import Supplier
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Tenant
from subscriptions.forms import AGREEMENT_OWNERSHIP_HELP, ProviderForm, SubscriptionForm
from subscriptions.models import Provider, SubscriptionTypeChoices

AGREEMENT_OWNERSHIP_MAPPING = (
    "Record one agreement in one module only. SaaS and cloud entitlement: record as Subscription. "
    "Support, maintenance, lease, warranty, SLA, or asset-covered service: record as Contract. "
    "Other recurring entitlement without asset/SLA coverage: record as Subscription. "
    "Other legal/commercial agreement: record as Contract."
)

AGREEMENT_ENTITLED_QUANTITY_LABEL = "Agreement Entitled Quantity"

AGREEMENT_ENTITLED_QUANTITY_HELP = (
    "Number of seats, users, or devices entitled by the vendor agreement. "
    "This value is independent of linked License seats."
)


class ProviderSupplierBridgeTests(TenantTestMixin, TestCase):
    """A Provider profile may carry one optional, shared Supplier link."""

    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="scv-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="scv-b")
        self.set_active_tenant(self.tenant)
        self.supplier = Supplier.objects.create(name="Dell", slug="scv-dell")

    def tearDown(self):
        set_current_user(None)
        self.clear_tenant_context()

    def test_supplier_link_is_optional(self):
        provider = Provider.objects.create(name="Unlinked Provider")

        self.assertIsNone(provider.supplier)

    def test_hard_deleting_the_supplier_keeps_the_provider_and_clears_the_link(self):
        provider = Provider.objects.create(name="Linked Provider", supplier=self.supplier)

        self.supplier.delete(force_hard_delete=True)
        provider.refresh_from_db()

        self.assertTrue(Provider.objects.filter(pk=provider.pk).exists())
        self.assertIsNone(provider.supplier)

    def test_scoped_providers_in_two_tenants_share_one_supplier(self):
        provider_a = Provider.objects.create(name="Reseller A", tenant=self.tenant, supplier=self.supplier)
        provider_b = Provider.objects.create(name="Reseller B", tenant=self.tenant_b, supplier=self.supplier)

        self.assertEqual(
            set(self.supplier.providers.values_list("pk", flat=True)),
            {provider_a.pk},
        )

        self.set_active_tenant(self.tenant_b)
        self.assertEqual(
            set(self.supplier.providers.values_list("pk", flat=True)),
            {provider_b.pk},
        )

        # Supplier itself is global reference data: exactly one row, visible
        # from both tenant scopes.
        self.assertEqual(set(Supplier.objects.values_list("pk", flat=True)), {self.supplier.pk})


class ProviderFormSupplierNameTests(TenantTestMixin, TestCase):
    """The Provider name is optional only when a Supplier is present."""

    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="scv-form-a")
        self.set_active_tenant(self.tenant)
        self.supplier = Supplier.objects.create(name="Dell", slug="scv-form-dell")

    def tearDown(self):
        set_current_user(None)
        self.clear_tenant_context()

    def test_new_provider_with_blank_name_takes_the_supplier_name(self):
        form = ProviderForm(data={"name": "", "supplier": self.supplier.pk, "is_active": True})

        self.assertTrue(form.is_valid(), form.errors)
        provider = form.save()

        self.assertEqual(provider.name, "Dell")
        self.assertEqual(provider.supplier, self.supplier)

    def test_new_provider_keeps_an_explicit_name(self):
        form = ProviderForm(data={"name": "Dell Direct", "supplier": self.supplier.pk, "is_active": True})

        self.assertTrue(form.is_valid(), form.errors)
        provider = form.save()

        self.assertEqual(provider.name, "Dell Direct")
        self.assertEqual(provider.supplier, self.supplier)

    def test_blank_name_without_a_supplier_is_still_required(self):
        form = ProviderForm(data={"name": "", "is_active": True})

        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("This field is required.", form.errors["name"])

    def test_existing_provider_keeps_its_stored_name_when_a_supplier_is_added(self):
        provider = Provider.objects.create(name="Adobe Reseller")

        form = ProviderForm(data={"name": "", "supplier": self.supplier.pk, "is_active": True}, instance=provider)

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        self.assertEqual(saved.name, "Adobe Reseller")
        self.assertEqual(saved.supplier, self.supplier)

    def test_supplier_choices_are_global_and_exclude_soft_deleted_rows(self):
        other = Supplier.objects.create(name="Global Other", slug="scv-form-other")
        deleted = Supplier.objects.create(name="Gone Vendor", slug="scv-form-gone")
        deleted.delete()
        expected = {self.supplier.pk, other.pk}

        self.assertEqual(
            set(ProviderForm().fields["supplier"].queryset.values_list("pk", flat=True)),
            expected,
        )

        # A bound non-superuser without any scope fails closed for tenant-scoped
        # models; Supplier is unscoped reference data and must stay complete.
        set_current_user(self.tenant_user)
        self.clear_tenant_context()
        self.assertEqual(
            set(ProviderForm().fields["supplier"].queryset.values_list("pk", flat=True)),
            expected,
        )


class AgreementOwnershipHelpTests(TestCase):
    """One agreement is recorded in exactly one module (issue #500)."""

    def test_subscription_type_help_carries_the_exclusive_mapping(self):
        self.assertEqual(str(AGREEMENT_OWNERSHIP_HELP), AGREEMENT_OWNERSHIP_MAPPING)
        self.assertEqual(
            str(SubscriptionForm().fields["type"].help_text),
            AGREEMENT_OWNERSHIP_MAPPING,
        )

    def test_external_agreement_reference_is_not_an_internal_contract_link(self):
        help_text = str(SubscriptionForm().fields["contract_reference"].help_text)

        self.assertIn("External vendor, PO, or agreement reference.", help_text)
        self.assertIn("It does not link a Procurement Contract", help_text)
        self.assertIn("must not be used to justify duplicating the same agreement", help_text)

    def test_subscription_surfaces_expose_no_internal_contract_link(self):
        field_names = set(SubscriptionForm().fields)

        # `vendor_contract_auto_renews` is the pre-existing auto-renewal boolean
        # (export alias `auto_renewal`), not a link to a Procurement Contract.
        self.assertEqual(
            {name for name in field_names if "contract" in name},
            {"contract_reference", "vendor_contract_auto_renews"},
        )

    def test_subscription_type_choices_are_unchanged(self):
        expected = [(value, str(label)) for value, label in SubscriptionTypeChoices.choices]

        self.assertEqual(
            [(value, str(label)) for value, label in SubscriptionForm().fields["type"].choices],
            expected,
        )


class SubscriptionSeatVocabularyTests(TestCase):
    """Agreement entitlement and computed license seats are distinct metrics."""

    def test_licensed_quantity_label_and_help_text_are_exact(self):
        field = SubscriptionForm().fields["licensed_quantity"]

        self.assertEqual(str(field.label), AGREEMENT_ENTITLED_QUANTITY_LABEL)
        self.assertEqual(str(field.help_text), AGREEMENT_ENTITLED_QUANTITY_HELP)
        self.assertNotIn("—", str(field.help_text))
        self.assertNotIn("–", str(field.help_text))

    def test_widget_allows_zero(self):
        form = SubscriptionForm()

        self.assertEqual(int(form.fields["licensed_quantity"].widget.attrs["min"]), 0)
        self.assertIn('min="0"', str(form["licensed_quantity"]))

    def test_bound_form_accepts_zero_and_blank(self):
        provider = Provider.objects.create(name="Seat Metric Provider")
        cases = (("0", 0), ("", None))

        for raw_value, expected in cases:
            with self.subTest(licensed_quantity=raw_value):
                form = SubscriptionForm(
                    data={
                        "name": "Seat Metric",
                        "provider": provider.pk,
                        "type": SubscriptionTypeChoices.SAAS,
                        "licensed_quantity": raw_value,
                    }
                )

                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["licensed_quantity"], expected)
