from __future__ import annotations

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.translation import override

from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm
from assets.forms.checkout_forms import AssetCheckOutForm
from assets.forms.request_forms import AssetRequestForm
from assets.models import Asset, AssetReservation, Warranty
from assets.models.choices import WarrantyTypeChoices
from inventory.forms.accessory_forms import AccessoryForm
from inventory.forms.component_forms import ComponentForm
from inventory.forms.consumable_forms import ConsumableForm
from licenses.forms import LicenseForm
from organization.tables import CostCenterTable
from procurement.models import Contract
from subscriptions.forms import SubscriptionCheckoutForm


class VisibleFormHelpTextTests(SimpleTestCase):
    """Model metadata keeps migration compatibility; forms own visible copy."""

    def test_visible_helptexts_use_humanized_dash_free_copy(self):
        expected = {
            (AssetForm, "depreciation_override"): (
                "Override the depreciation policy. Leave empty to use the tenant default or asset-type schedule."
            ),
            (AssetTypeForm, "ean"): "Barcode (EAN, UPC, or GTIN). Scan this barcode to view assets of this type.",
            (AccessoryForm, "ean"): "Barcode (EAN, UPC, or GTIN). Scan it to open this item.",
            (ConsumableForm, "ean"): "Barcode (EAN, UPC, or GTIN). Scan it to open this item.",
            (ComponentForm, "ean"): "Barcode (EAN, UPC, or GTIN). Scan it to open this item.",
            (LicenseForm, "version"): (
                "Optional version constraint for this license entitlement (e.g. '2021', '16.x'). "
                "Reconciliation ignores the version and runs at the Software level."
            ),
        }
        for (form_class, field_name), text in expected.items():
            with self.subTest(form=form_class.__name__, field=field_name):
                rendered_help_text = str(form_class.base_fields[field_name].help_text)
                self.assertEqual(rendered_help_text, text)
                self.assertNotIn("—", rendered_help_text)
                self.assertNotIn("–", rendered_help_text)

    def test_new_form_copy_is_translated_and_dash_free(self):
        with override("de"):
            requestable = [str(label) for _, label in AssetForm.base_fields["requestable"].choices]
            self.assertEqual(
                requestable,
                ["Vom Asset-Typ übernehmen (Standard)", "Ja, anforderbar", "Nein, nicht anforderbar"],
            )
            self.assertEqual(
                str(AssetForm.base_fields["asset_tag"].widget.attrs["placeholder"]),
                "Leer lassen, um automatisch zu generieren",
            )
            self.assertEqual(
                str(AssetForm.base_fields["warranty_type"].choices[0][1]),
                "Garantieart auswählen",
            )
            label = str(
                CostCenterTable([]).render_parent(
                    SimpleNamespace(code="FIN", name="Finance", get_absolute_url=lambda: "/cost-centers/1/")
                )
            )
            self.assertIn("FIN: Finance", label)
            self.assertNotIn("—", label)
            self.assertNotIn("–", label)

    def test_target_type_choices_are_translated(self):
        expected = {
            AssetCheckOutForm: ["Asset-Inhaber", "Lagerort", "Asset"],
            AssetRequestForm: ["Ich selbst", "Asset-Inhaber", "Lagerort", "Asset"],
            SubscriptionCheckoutForm: ["Mitarbeiter / Asset-Inhaber", "Hardware-Asset", "Lagerort"],
        }
        with override("de"):
            for form_class, labels in expected.items():
                with self.subTest(form=form_class.__name__):
                    actual = [str(label) for _, label in form_class.base_fields["target_type"].choices]
                    self.assertEqual(actual, labels)

    def test_subscription_renewal_days_use_german_singular_and_plural(self):
        values = {
            "provider": None,
            "type": "saas",
            "status": "active",
            "get_type_display": lambda: "SaaS",
            "get_status_display": lambda: "Active",
            "vendor_contract_auto_renews": False,
            "tenant": None,
            "owner": None,
            "start_date": None,
            "term_months": None,
            "billing_cycle": None,
            "renewal_date": None,
            "cancellation_date": None,
            "contract_reference": None,
            "cost_center": None,
            "renewal_cost": None,
            "annual_cost": None,
            "licensed_quantity": None,
            "currency": "EUR",
            "days_until_renewal": 1,
        }
        with override("de"):
            singular = render_to_string(
                "subscriptions/includes/detail/subscription_info.html", {"object": SimpleNamespace(**values)}
            )
            values["days_until_renewal"] = 2
            plural = render_to_string(
                "subscriptions/includes/detail/subscription_info.html", {"object": SimpleNamespace(**values)}
            )
            values["days_until_renewal"] = -1
            overdue_singular = render_to_string(
                "subscriptions/includes/detail/subscription_info.html", {"object": SimpleNamespace(**values)}
            )
            values["days_until_renewal"] = -2
            overdue_plural = render_to_string(
                "subscriptions/includes/detail/subscription_info.html", {"object": SimpleNamespace(**values)}
            )
        self.assertIn("1 Tag", singular)
        self.assertNotIn("1 Tage", singular)
        self.assertIn("2 Tage", plural)
        self.assertIn("1 Tag überfällig", overdue_singular)
        self.assertNotIn("1 Tage überfällig", overdue_singular)
        self.assertIn("2 Tage überfällig", overdue_plural)


class StablePresentationContractTests(SimpleTestCase):
    def test_asset_status_display_keeps_the_current_model_contract(self):
        self.assertEqual(Asset(status=None).get_status_display(), "Not set")

    def test_contract_display_keeps_the_legacy_api_value(self):
        self.assertEqual(str(Contract(contract_number="CTR-001", name="Support")), "CTR-001 – Support")

    def test_lifecycle_strings_keep_the_legacy_model_contract(self):
        import datetime

        asset = Asset(name="Laptop", asset_tag="ASSET-001")
        warranty = Warranty(
            asset=asset,
            warranty_type=WarrantyTypeChoices.HARDWARE,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )
        reservation = AssetReservation(
            asset=asset,
            reserved_for=None,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 1, 2),
        )

        self.assertEqual(str(warranty), "Hardware warranty on Laptop (ASSET-001) (2026-01-01 – 2027-01-01)")
        self.assertEqual(str(reservation), "Laptop (ASSET-001) reserved for (no holder) (2026-01-01 – 2026-01-02)")
