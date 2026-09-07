import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from assets.forms.asset_form import AssetForm
from extras.models import CustomField

APP_ROOT = Path(__file__).resolve().parents[2]


class AssetFormPresenceTests(SimpleTestCase):
    def test_explicit_null_presence_is_preserved_as_a_set_value(self):
        definition = SimpleNamespace(
            name="instance_note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            nullable=True,
        )
        form = object.__new__(AssetForm)
        form.custom_field_presence_keys = {"cf_instance_note": "cf_instance_note__presence"}
        form.custom_field_clear_keys = {}
        form.custom_field_definitions = {"cf_instance_note": definition}
        form.add_error = lambda *args: self.fail("explicit null must not add a presence error")
        cleaned_data = {
            "cf_instance_note": "draft value",
            "cf_instance_note__presence": "null",
        }

        AssetForm._apply_t15_presence(form, cleaned_data)

        self.assertIsNone(cleaned_data["cf_instance_note"])

    def test_asset_draft_transport_rehydrates_a_field_after_type_switch(self):
        from django.http import QueryDict

        definition = SimpleNamespace(
            name="instance_note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            nullable=True,
            label="Instance note",
            help_text="",
            required=False,
            text_max_length=255,
            regex="",
            validation_rule=None,
        )
        resolved = SimpleNamespace(definition=definition, read_only=False, provenance=("local/notes",))
        form = object.__new__(AssetForm)
        form.is_bound = True
        form.data = QueryDict("specification_draft__cf_instance_note=%22edited%20draft%22")
        form.instance = SimpleNamespace(pk=None, custom_field_data={})
        form.fields = {}
        form.helper = SimpleNamespace(layout=None)
        form._build_t15_presentation = lambda *args: None

        with patch("assets.forms.asset_form.resolve_asset_custom_fields", return_value=[resolved]):
            AssetForm._configure_custom_fields(form, SimpleNamespace())

        self.assertEqual(form.fields["cf_instance_note"].initial, "edited draft")
        self.assertEqual(form.data.get("cf_instance_note"), "edited draft")
        self.assertEqual(form.data.get("cf_instance_note__presence"), "value")


class T15SpecificationUiContractTests(unittest.TestCase):
    def test_asset_type_form_declares_presence_aware_composition_metadata(self):
        source = (APP_ROOT / "assets" / "forms" / "assettype_form.py").read_text(encoding="utf-8")
        self.assertIn("specification_fieldsets_presence", source)
        self.assertIn("specification_sections", source)
        self.assertIn("specification_history", source)

    def test_asset_form_declares_model_and_history_presentation_metadata(self):
        source = (APP_ROOT / "assets" / "forms" / "asset_form.py").read_text(encoding="utf-8")
        self.assertIn("model_specification_fields", source)
        self.assertIn("specification_history", source)
        self.assertIn("custom_field_presence_keys", source)

    def test_specification_templates_expose_real_editor_hooks(self):
        asset_type_template = (APP_ROOT / "templates" / "assets" / "_assettype_specification_form.html").read_text(
            encoding="utf-8"
        )
        asset_template = (APP_ROOT / "templates" / "assets" / "_asset_specification_form.html").read_text(
            encoding="utf-8"
        )
        for template in (asset_type_template, asset_template):
            self.assertIn("data-specification-editor", template)
            self.assertIn("data-specification-history", template)
            self.assertIn("Previous specification values", template)
        self.assertIn("data-specification-fieldset", asset_type_template)
        self.assertIn("data-specification-copy-model-key", asset_template)


if __name__ == "__main__":
    unittest.main()
