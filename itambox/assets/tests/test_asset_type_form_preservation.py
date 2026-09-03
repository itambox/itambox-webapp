from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.forms import AssetTypeForm
from assets.models import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


class AssetTypeFormPreservationTests(TestCase):
    def test_unbound_generic_asset_type_field_is_rendered(self):
        field = CustomField.objects.create(
            name="generic_asset_type_spec",
            namespace="local",
            label="Generic Asset Type specification",
            scope=None,
        )
        field.object_types.add(ContentType.objects.get_for_model(AssetType))

        form = AssetTypeForm()

        self.assertIn("cf_generic_asset_type_spec", form.fields)
        self.assertIn("cf_generic_asset_type_spec", form.custom_field_keys)

    def test_duplicate_submitted_fieldset_ids_are_a_form_error(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="specifications",
            label="Specifications",
        )
        form = AssetTypeForm(
            data={
                "manufacturer": manufacturer.pk,
                "model": "Device",
                "slug": "example-device",
                "custom_fieldsets": [fieldset.pk, fieldset.pk],
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("custom_fieldsets", form.errors)

    def test_new_draft_copies_ordered_category_defaults_once(self):
        category = Category.objects.create(name="Servers", slug="servers")
        first = CustomFieldset.objects.create(
            namespace="local",
            slug="compute",
            label="Compute",
        )
        second = CustomFieldset.objects.create(
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
        hidden = CustomField.objects.create(
            name="hidden_spec",
            namespace="local",
            label="Hidden specification",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="specifications",
            label="Specifications",
        )
        hidden_fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="hidden-specifications",
            label="Hidden Specifications",
        )
        CustomFieldsetField.objects.create(fieldset=hidden_fieldset, custom_field=hidden, position=10)
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
