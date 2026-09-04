from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.cookie import CookieStorage
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from assets.forms import AssetTypeForm
from assets.models import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
from itambox.views.generic import ObjectEditView


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

    def test_category_defaults_survive_soft_delete_and_are_removed_by_hard_purge(self):
        category = Category.objects.create(name="Category with defaults", slug="category-with-defaults")
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="category-defaults",
            label="Category defaults",
        )
        membership = CategoryDefaultFieldset.objects.create(category=category, fieldset=fieldset, position=10)

        category.delete()
        category.refresh_from_db()
        self.assertIsNotNone(category.deleted_at)
        self.assertEqual(CategoryDefaultFieldset.objects.get(pk=membership.pk).position, 10)

        category.restore()
        category.refresh_from_db()
        self.assertIsNone(category.deleted_at)
        self.assertEqual(CategoryDefaultFieldset.objects.get(pk=membership.pk).position, 10)

        category_pk = category.pk
        category.delete(force_hard_delete=True)
        self.assertFalse(Category._base_manager.filter(pk=category_pk).exists())
        self.assertFalse(CategoryDefaultFieldset.objects.filter(pk=membership.pk).exists())

    def test_html_edit_preserves_scalar_and_fieldset_changes_under_lock(self):
        manufacturer = Manufacturer.objects.create(name="Lock Example", slug="lock-example")
        first = CustomFieldset.objects.create(namespace="local", slug="first-lock", label="First lock")
        second = CustomFieldset.objects.create(namespace="local", slug="second-lock", label="Second lock")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Old model",
            slug="old-model",
            description="Old description",
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=first, position=10)
        form_data = {
            "manufacturer": manufacturer.pk,
            "model": "New model",
            "slug": "old-model",
            "description": "New description",
            "custom_fieldsets": [str(second.pk)],
        }
        form = AssetTypeForm(data=form_data, instance=asset_type)
        self.assertTrue(form.is_valid(), form.errors)
        view = ObjectEditView()
        view.model = AssetType
        view.object = asset_type
        view.request = RequestFactory().post("/", data={**form_data, "return_url": "/"})
        view.request._messages = CookieStorage(view.request)

        response = view.form_valid(form)

        self.assertEqual(response.status_code, 302)
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.model, "New model")
        self.assertEqual(asset_type.description, "New description")
        self.assertEqual(list(asset_type.fieldset_memberships.values_list("fieldset_id", flat=True)), [second.pk])

    def test_composition_change_with_missing_required_field_is_atomic(self):
        manufacturer = Manufacturer.objects.create(name="Atomic Example", slug="atomic-example")
        old_fieldset = CustomFieldset.objects.create(namespace="local", slug="old-composition", label="Old")
        new_fieldset = CustomFieldset.objects.create(namespace="local", slug="new-composition", label="New")
        required = CustomField.objects.create(
            name="required_after_composition",
            namespace="local",
            label="Required after composition",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET_TYPE,
            required=True,
        )
        CustomFieldsetField.objects.create(fieldset=new_fieldset, custom_field=required, position=10)
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Old model",
            slug="atomic-example",
            description="Old description",
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=old_fieldset, position=10)

        form = AssetTypeForm(
            data={
                "manufacturer": manufacturer.pk,
                "model": "New model",
                "slug": "atomic-example",
                "description": "New description",
                "custom_fieldsets": [new_fieldset.pk],
            },
            instance=asset_type,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("cf_required_after_composition", form.errors)
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.model, "Old model")
        self.assertEqual(asset_type.description, "Old description")
        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset_id", flat=True)),
            [old_fieldset.pk],
        )

    def test_asset_type_form_prefetches_selected_fieldsets_in_constant_queries(self):
        fieldsets = []
        for index in range(3):
            fieldset = CustomFieldset.objects.create(
                namespace="local",
                slug=f"query-bound-{index}",
                label=f"Query bound {index}",
            )
            field = CustomField.objects.create(
                name=f"query_bound_{index}",
                namespace="local",
                label=f"Query bound {index}",
                scope=CustomField.SCOPE_ASSET_TYPE,
            )
            CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=10)
            fieldsets.append(fieldset)
        asset_type = AssetType.objects.create(
            manufacturer=Manufacturer.objects.create(name="Query Bound Manufacturer", slug="query-bound-manufacturer"),
            model="Query bound type",
            slug="query-bound-type",
        )
        AssetTypeFieldset.objects.bulk_create(
            [
                AssetTypeFieldset(asset_type=asset_type, fieldset=fieldset, position=(index + 1) * 10)
                for index, fieldset in enumerate(fieldsets)
            ]
        )

        with CaptureQueriesContext(connection) as queries:
            form = AssetTypeForm(instance=asset_type)

        self.assertEqual(len(form.custom_field_keys), 3)
        self.assertLessEqual(len(queries), 9)

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
