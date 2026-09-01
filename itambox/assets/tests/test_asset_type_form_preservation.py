from django.test import TestCase

from assets.forms import AssetTypeForm
from assets.models import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


class AssetTypeFormPreservationTests(TestCase):
    def test_new_draft_copies_ordered_category_defaults_once(self):
        category = Category.objects.create(name="Servers", slug="servers")
        first = CustomFieldset.objects.create(
            name="Compute",
            namespace="local",
            slug="compute",
            label="Compute",
        )
        second = CustomFieldset.objects.create(
            name="Physical",
            namespace="local",
            slug="physical",
            label="Physical",
        )
        CategoryDefaultFieldset.objects.create(category=category, fieldset=first, position=20)
        CategoryDefaultFieldset.objects.create(category=category, fieldset=second, position=10)

        draft = AssetTypeForm(initial={"category": category.pk})
        explicit_empty = AssetTypeForm(data={"category": category.pk, "custom_fieldsets": []})

        self.assertEqual(draft.fields["custom_fieldsets"].initial, [second.pk, first.pk])
        self.assertEqual(explicit_empty.fields["custom_fieldsets"].initial, [])

    def test_new_draft_category_without_defaults_starts_empty(self):
        category = Category.objects.create(name="Empty", slug="empty")

        draft = AssetTypeForm(initial={"category": category.pk})

        self.assertEqual(draft.fields["custom_fieldsets"].initial, [])

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
