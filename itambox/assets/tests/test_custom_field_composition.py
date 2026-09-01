from django.test import TestCase
from django.utils import timezone

from assets.customfields import resolve_asset_custom_fields, resolve_asset_type_custom_fields
from assets.models import AssetType, AssetTypeFieldset, Manufacturer
from extras.customfields import apply_custom_field_patch
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


class CustomFieldCompositionTests(TestCase):
    def setUp(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        self.asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Device", slug="example-device")
        self.first = CustomFieldset.objects.create(
            name="First",
            namespace="local",
            slug="first",
            label="First",
        )
        self.second = CustomFieldset.objects.create(
            name="Second",
            namespace="local",
            slug="second",
            label="Second",
        )
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.first, position=10)
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.second, position=20)

    def _field(self, name, scope, **kwargs):
        return CustomField.objects.create(name=name, namespace="local", label=name, scope=scope, **kwargs)

    def test_resolver_orders_deduplicates_scopes_and_retains_deprecated_values(self):
        first_only = self._field("first_only", CustomField.SCOPE_ASSET_TYPE)
        shared = self._field("shared", CustomField.SCOPE_BOTH)
        asset_only = self._field("asset_only", CustomField.SCOPE_ASSET)
        deprecated = self._field(
            "deprecated_value",
            CustomField.SCOPE_ASSET_TYPE,
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )
        global_asset = self._field("global_asset", CustomField.SCOPE_ASSET)
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
        deleted_field = self._field("deleted_fieldset_value", CustomField.SCOPE_ASSET_TYPE)
        deprecated_field = self._field("deprecated_fieldset_value", CustomField.SCOPE_ASSET_TYPE)
        CustomFieldsetField.objects.create(fieldset=self.first, custom_field=deleted_field, position=10)
        CustomFieldsetField.objects.create(fieldset=self.second, custom_field=deprecated_field, position=10)
        stored = {
            "deleted_fieldset_value": "preserve deleted",
            "deprecated_fieldset_value": "preserve deprecated",
        }
        self.asset_type.custom_field_data = stored
        self.asset_type.save(update_fields=["custom_field_data"])
        CustomFieldset.all_objects.filter(pk=self.first.pk).update(deleted_at=timezone.now())
        self.second.lifecycle = CustomFieldset.LIFECYCLE_DEPRECATED
        self.second.save(update_fields=["lifecycle"])

        self.assertEqual(resolve_asset_type_custom_fields(self.asset_type), [])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, stored)

    def test_patch_preserves_unrendered_values_and_distinguishes_false_zero_empty_and_null(self):
        flag = self._field("flag", CustomField.SCOPE_ASSET, field_type=CustomField.FIELD_TYPE_BOOLEAN)
        count = self._field("count", CustomField.SCOPE_ASSET, field_type=CustomField.FIELD_TYPE_INTEGER)
        note = self._field("note", CustomField.SCOPE_ASSET, field_type=CustomField.FIELD_TYPE_TEXT)
        capacity = self._field(
            "capacity",
            CustomField.SCOPE_ASSET,
            field_type=CustomField.FIELD_TYPE_DECIMAL,
            decimal_scale=3,
        )
        nullable = self._field("nullable_note", CustomField.SCOPE_ASSET, nullable=True)
        to_clear = self._field("to_clear", CustomField.SCOPE_ASSET)
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
