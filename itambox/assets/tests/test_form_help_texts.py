from __future__ import annotations

from django.test import SimpleTestCase

from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm
from inventory.forms.accessory_forms import AccessoryForm
from inventory.forms.component_forms import ComponentForm
from inventory.forms.consumable_forms import ConsumableForm
from licenses.forms import LicenseForm


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
                "Optional version constraint for this license entitlement (e.g. '2021', '16.x'). This is for "
                "reference only; reconciliation is performed at the Software level and ignores versions."
            ),
        }
        for (form_class, field_name), text in expected.items():
            with self.subTest(form=form_class.__name__, field=field_name):
                self.assertEqual(str(form_class.base_fields[field_name].help_text), text)
                self.assertNotIn("—", text)
                self.assertNotIn("–", text)
