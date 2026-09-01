from django.test import TestCase

from assets.forms import AssetTypeForm
from assets.models import AssetType, AssetTypeFieldset, Manufacturer
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


class AssetTypeFormPreservationTests(TestCase):
    def test_plural_composition_update_preserves_unrendered_and_unknown_values(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        visible = CustomField.objects.create(
            name="visible_spec",
            namespace="local",
            label="Visible specification",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        CustomField.objects.create(
            name="hidden_spec",
            namespace="local",
            label="Hidden specification",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        fieldset = CustomFieldset.objects.create(
            name="Specifications",
            namespace="local",
            slug="specifications",
            label="Specifications",
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=visible, position=10)
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Device",
            slug="example-device",
            custom_field_data={"visible_spec": "old", "hidden_spec": "keep", "unknown": "keep"},
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)

        form = AssetTypeForm(
            data={
                "manufacturer": manufacturer.pk,
                "model": "Device",
                "slug": "example-device",
                "custom_fieldsets": [fieldset.pk],
                "cf_visible_spec": "updated",
            },
            instance=asset_type,
        )

        self.assertNotIn("custom_fieldset", form.fields)
        self.assertIn("custom_fieldsets", form.fields)
        self.assertIn("cf_visible_spec", form.fields)
        self.assertNotIn("cf_hidden_spec", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(
            saved.custom_field_data,
            {"visible_spec": "updated", "hidden_spec": "keep", "unknown": "keep"},
        )
