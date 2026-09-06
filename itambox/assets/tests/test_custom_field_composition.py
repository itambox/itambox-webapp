from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import Prefetch
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from assets.customfields import resolve_asset_custom_fields, resolve_asset_type_custom_fields
from assets.models import Asset, AssetType, AssetTypeFieldset, Manufacturer
from extras.customfields import apply_custom_field_patch, build_custom_field_form_field, validate_custom_field_value
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField


class CustomFieldCompositionTests(TestCase):
    def setUp(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        self.asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Device", slug="example-device")
        self.first = CustomFieldset.objects.create(
            namespace="local",
            slug="first",
            label="First",
        )
        self.second = CustomFieldset.objects.create(
            namespace="local",
            slug="second",
            label="Second",
        )
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.first, position=10)
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.second, position=20)

    def _field(self, name, activation, *target_models, **kwargs):
        field = CustomField.objects.create(
            name=name,
            namespace="local",
            label=name,
            activation=activation,
            **kwargs,
        )
        for target_model in target_models:
            field.object_types.add(ContentType.objects.get_for_model(target_model))
        return field

    def test_resolver_uses_prefetched_composition_without_extra_queries(self):
        field = self._field("prefetched_field", CustomField.ACTIVATION_COMPOSED, AssetType)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=field, position=10)
        asset_type = AssetType.objects.prefetch_related(
            Prefetch(
                "fieldset_memberships",
                queryset=AssetTypeFieldset.objects.select_related("fieldset").prefetch_related(
                    "fieldset__field_memberships__custom_field__object_types"
                ),
            )
        ).get(pk=self.asset_type.pk)

        with CaptureQueriesContext(connection) as queries:
            resolved = resolve_asset_type_custom_fields(asset_type)

        self.assertEqual([item.definition.name for item in resolved], ["prefetched_field"])
        # One set-based query resolves unbound globals; composed memberships add no per-field queries.
        self.assertEqual(len(queries), 1)

    def test_prefetched_choice_values_do_not_add_per_field_queries(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="prefetched-choice-values",
            label="Prefetched choice values",
        )
        CustomFieldChoice.objects.create(choice_set=choice_set, key="one", label="One", position=10)
        choice_field = self._field(
            "prefetched_choice",
            CustomField.ACTIVATION_COMPOSED,
            AssetType,
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            choice_set=choice_set,
            max_values=1,
        )
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=choice_field, position=10)
        asset_type = AssetType.objects.prefetch_related(
            Prefetch(
                "fieldset_memberships",
                queryset=AssetTypeFieldset.objects.select_related("fieldset").prefetch_related(
                    "fieldset__field_memberships__custom_field__object_types",
                    "fieldset__field_memberships__custom_field__choice_set__choices",
                ),
            )
        ).get(pk=self.asset_type.pk)

        with CaptureQueriesContext(connection) as queries:
            resolved = resolve_asset_type_custom_fields(asset_type)
            form_field = build_custom_field_form_field(resolved[0].definition)
            self.assertEqual(validate_custom_field_value(resolved[0], "one"), "one")

        self.assertEqual(form_field.choices, [("", "---------"), ("one", "One")])
        self.assertEqual(len(queries), 1)

    def test_resolver_orders_deduplicates_scopes_and_retains_deprecated_values(self):
        first_only = self._field("first_only", CustomField.ACTIVATION_COMPOSED, AssetType)
        shared = self._field("shared", CustomField.ACTIVATION_COMPOSED, AssetType, Asset)
        asset_only = self._field("asset_only", CustomField.ACTIVATION_COMPOSED, Asset)
        deprecated = self._field(
            "deprecated_value",
            CustomField.ACTIVATION_COMPOSED,
            AssetType,
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )
        global_asset = self._field("global_asset", CustomField.ACTIVATION_GLOBAL, Asset)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=first_only, position=10)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=shared, position=20)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=deprecated, position=30)
        CustomFieldsetField.objects.create(fieldset=self.second, custom_field=shared, position=10)
        CustomFieldsetField.objects.create(fieldset=self.second, custom_field=asset_only, position=20)
        self.asset_type.custom_field_data = {"deprecated_value": "retained", "unknown": "preserved"}
        self.asset_type.save(update_fields=["custom_field_data"])

        type_fields = resolve_asset_type_custom_fields(self.asset_type)
        self.assertEqual([item.definition.name for item in type_fields], ["first_only", "shared", "deprecated_value"])
        self.assertEqual(type_fields[1].provenance, ("local/first", "local/second"))
        self.assertTrue(type_fields[2].read_only)

        asset_fields = resolve_asset_custom_fields(self.asset_type)
        self.assertEqual([item.definition.name for item in asset_fields], ["shared", "asset_only", global_asset.name])

    def test_resolver_omits_fields_from_inactive_fieldsets_without_removing_values(self):
        deleted_field = self._field("deleted_fieldset_value", CustomField.ACTIVATION_COMPOSED, AssetType)
        deprecated_field = self._field("deprecated_fieldset_value", CustomField.ACTIVATION_COMPOSED, AssetType)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=deleted_field, position=10)
        CustomFieldsetField.objects.create(fieldset=self.second, custom_field=deprecated_field, position=10)
        stored = {
            "deleted_fieldset_value": "preserve deleted",
            "deprecated_fieldset_value": "preserve deprecated",
        }
        self.asset_type.custom_field_data = stored
        self.asset_type.save(update_fields=["custom_field_data"])
        self.first.lifecycle = CustomFieldset.LIFECYCLE_DEPRECATED
        self.first.deprecated_at = timezone.now()
        self.first.save(update_fields=["lifecycle", "deprecated_at"])
        self.second.lifecycle = CustomFieldset.LIFECYCLE_DEPRECATED
        self.second.deprecated_at = timezone.now()
        self.second.save(update_fields=["lifecycle", "deprecated_at"])

        self.assertEqual(resolve_asset_type_custom_fields(self.asset_type), [])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, stored)

    def test_patch_preserves_unrendered_values_and_distinguishes_false_zero_empty_and_null(self):
        flag = self._field("flag", CustomField.ACTIVATION_GLOBAL, Asset, field_type=CustomField.FIELD_TYPE_BOOLEAN)
        count = self._field("count", CustomField.ACTIVATION_GLOBAL, Asset, field_type=CustomField.FIELD_TYPE_INTEGER)
        note = self._field("note", CustomField.ACTIVATION_GLOBAL, Asset, field_type=CustomField.FIELD_TYPE_TEXT)
        capacity = self._field(
            "capacity",
            CustomField.ACTIVATION_GLOBAL,
            Asset,
            field_type=CustomField.FIELD_TYPE_DECIMAL,
            decimal_scale=3,
        )
        nullable = self._field("nullable_note", CustomField.ACTIVATION_GLOBAL, Asset, nullable=True)
        to_clear = self._field("to_clear", CustomField.ACTIVATION_GLOBAL, Asset)
        existing = {"hidden": "keep", "removed": "keep", "to_clear": "remove"}

        merged = apply_custom_field_patch(
            existing,
            [flag, count, note, capacity, nullable, to_clear],
            {"flag": False, "count": 0, "note": "", "capacity": "16", "nullable_note": None},
            clear_keys=["to_clear"],
        )

        self.assertEqual(
            merged,
            {
                "hidden": "keep",
                "removed": "keep",
                "flag": False,
                "count": 0,
                "note": "",
                "capacity": "16.000",
                "nullable_note": None,
            },
        )
