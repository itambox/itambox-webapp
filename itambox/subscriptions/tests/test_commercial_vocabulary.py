"""Issue #508: subscription commercial fields use the shared supplier catalogue."""

from django.test import TestCase

from assets.models import Supplier
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from subscriptions.forms import AGREEMENT_OWNERSHIP_HELP, SubscriptionForm
from subscriptions.models import SubscriptionTypeChoices

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


class SubscriptionCommercialFieldsTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="scv-a")
        self.set_active_tenant(self.tenant)
        self.supplier = Supplier.objects.create(name="Dell", slug="scv-dell")

    def tearDown(self):
        set_current_user(None)
        self.clear_tenant_context()

    def test_supplier_is_required_and_inactive_suppliers_are_unselectable(self):
        inactive = Supplier.objects.create(name="Retired Vendor", slug="scv-retired", is_active=False)
        form = SubscriptionForm()

        self.assertTrue(form.fields["supplier"].required)
        self.assertIn(self.supplier, form.fields["supplier"].queryset)
        self.assertNotIn(inactive, form.fields["supplier"].queryset)

    def test_linked_contract_is_optional_and_uses_tom_select(self):
        form = SubscriptionForm()

        self.assertFalse(form.fields["linked_contract"].required)
        self.assertEqual(form.fields["linked_contract"].widget.attrs["data-tom-select"], "")

    def test_external_agreement_reference_is_not_an_internal_contract_link(self):
        help_text = str(SubscriptionForm().fields["contract_reference"].help_text)

        self.assertIn("External vendor, PO, or agreement reference.", help_text)
        self.assertIn("It does not link a Procurement Contract", help_text)
        self.assertIn("must not be used to justify duplicating the same agreement", help_text)

    def test_subscription_form_exposes_both_commercial_links(self):
        field_names = set(SubscriptionForm().fields)

        self.assertIn("supplier", field_names)
        self.assertIn("contract_reference", field_names)
        self.assertIn("linked_contract", field_names)
        self.assertIn("vendor_contract_auto_renews", field_names)


class AgreementOwnershipHelpTests(TestCase):
    """One agreement is recorded in exactly one module (issue #500)."""

    def test_subscription_type_help_carries_the_exclusive_mapping(self):
        self.assertEqual(str(AGREEMENT_OWNERSHIP_HELP), AGREEMENT_OWNERSHIP_MAPPING)
        self.assertEqual(str(SubscriptionForm().fields["type"].help_text), AGREEMENT_OWNERSHIP_MAPPING)

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
        self.assertNotIn("???", str(field.help_text))
        self.assertNotIn("???", str(field.help_text))

    def test_widget_allows_zero(self):
        form = SubscriptionForm()

        self.assertEqual(int(form.fields["licensed_quantity"].widget.attrs["min"]), 0)
        self.assertIn('min="0"', str(form["licensed_quantity"]))

    def test_bound_form_accepts_zero_and_blank(self):
        supplier = Supplier.objects.create(name="Seat Metric Supplier", slug="seat-metric-supplier")
        cases = (("0", 0), ("", None))

        for raw_value, expected in cases:
            with self.subTest(licensed_quantity=raw_value):
                form = SubscriptionForm(
                    data={
                        "name": "Seat Metric",
                        "supplier": supplier.pk,
                        "type": SubscriptionTypeChoices.SAAS,
                        "licensed_quantity": raw_value,
                    }
                )

                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["licensed_quantity"], expected)
