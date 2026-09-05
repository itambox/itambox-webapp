from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.cookie import CookieStorage
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from assets.forms import AssetTypeForm
from assets.models import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
)
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

        # The bound covers the constant AssetType load plus the prefetched
        # membership graph (fieldsets -> memberships -> custom field ->
        # object types and choice sets -> choices). Both bounds below assert
        # this constant graph at two fixture sizes: doubling the selected
        # fieldsets must not add per-fieldset or per-field queries, which would
        # break the nine-query ceiling.
        with CaptureQueriesContext(connection) as queries:
            form = AssetTypeForm(instance=asset_type)
        self.assertEqual(len(form.custom_field_keys), 3)
        self.assertLessEqual(len(queries), 9)

        for index in range(3, 6):
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
            AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=(index + 1) * 10)

        with CaptureQueriesContext(connection) as queries:
            enlarged_form = AssetTypeForm(instance=asset_type)
        self.assertEqual(len(enlarged_form.custom_field_keys), 6)
        self.assertLessEqual(len(queries), 9)

    def test_asset_type_form_choice_heavy_composition_keeps_constant_queries(self):
        def build(size):
            manufacturer = Manufacturer.objects.create(name=f"Choice Bound {size}", slug=f"choice-bound-{size}")
            asset_type = AssetType.objects.create(
                manufacturer=manufacturer,
                model=f"Choice bound {size}",
                slug=f"choice-bound-type-{size}",
            )
            for fieldset_index in range(size):
                fieldset = CustomFieldset.objects.create(
                    namespace="local",
                    slug=f"choice-heavy-{size}-{fieldset_index}",
                    label=f"Choice heavy {size} {fieldset_index}",
                )
                text_field = CustomField.objects.create(
                    name=f"cf_text_{size}_{fieldset_index}",
                    namespace="local",
                    label=f"Text {fieldset_index}",
                    scope=CustomField.SCOPE_ASSET_TYPE,
                )
                integer_field = CustomField.objects.create(
                    name=f"cf_int_{size}_{fieldset_index}",
                    namespace="local",
                    label=f"Integer {fieldset_index}",
                    scope=CustomField.SCOPE_ASSET_TYPE,
                    field_type=CustomField.FIELD_TYPE_INTEGER,
                )
                single_set = CustomFieldChoiceSet.objects.create(
                    namespace="local",
                    slug=f"single-{size}-{fieldset_index}",
                    label=f"Single {fieldset_index}",
                )
                for choice_index in range(3):
                    CustomFieldChoice.objects.create(
                        choice_set=single_set,
                        key=f"single-{size}-{fieldset_index}-{choice_index}",
                        label=f"Single choice {choice_index}",
                        position=10 + choice_index,
                    )
                single_field = CustomField.objects.create(
                    name=f"cf_single_{size}_{fieldset_index}",
                    namespace="local",
                    label=f"Single {fieldset_index}",
                    scope=CustomField.SCOPE_ASSET_TYPE,
                    field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
                    choice_set=single_set,
                    max_values=1,
                )
                multi_set = CustomFieldChoiceSet.objects.create(
                    namespace="local",
                    slug=f"multi-{size}-{fieldset_index}",
                    label=f"Multi {fieldset_index}",
                )
                for choice_index in range(4):
                    CustomFieldChoice.objects.create(
                        choice_set=multi_set,
                        key=f"multi-{size}-{fieldset_index}-{choice_index}",
                        label=f"Multi choice {choice_index}",
                        position=10 + choice_index,
                    )
                multi_field = CustomField.objects.create(
                    name=f"cf_multi_{size}_{fieldset_index}",
                    namespace="local",
                    label=f"Multi {fieldset_index}",
                    scope=CustomField.SCOPE_ASSET_TYPE,
                    field_type=CustomField.FIELD_TYPE_MULTI_SELECT,
                    choice_set=multi_set,
                    max_values=2,
                )
                CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=text_field, position=10)
                CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=integer_field, position=20)
                CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=single_field, position=30)
                CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=multi_field, position=40)
                AssetTypeFieldset.objects.create(
                    asset_type=asset_type,
                    fieldset=fieldset,
                    position=(fieldset_index + 1) * 10,
                )
            return asset_type

        # The same constant query bound must hold when the composition graph is
        # choice-heavy: fieldsets -> memberships -> custom fields -> object
        # types and choice sets -> choices. Doubling fieldsets, select fields,
        # choice sets, and choices must not add per-fieldset, per-field, or
        # per-choice queries. The bound is 11 (not 9) because a non-null
        # choice set actually exercises the two prefetch levels that the
        # text-only fixture skips (Django skips a prefetch chain level whose
        # parent results are empty): one query for the choice sets and one
        # for their choices. Both are constant by construction, so the bound
        # holds at every fixture size.
        small = build(2)
        with CaptureQueriesContext(connection) as queries:
            small_form = AssetTypeForm(instance=small)
        self.assertEqual(len(small_form.custom_field_keys), 8)
        self.assertEqual(len(queries), 11)

        large = build(4)
        with CaptureQueriesContext(connection) as queries:
            large_form = AssetTypeForm(instance=large)
        self.assertEqual(len(large_form.custom_field_keys), 16)
        self.assertEqual(len(queries), 11)

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
