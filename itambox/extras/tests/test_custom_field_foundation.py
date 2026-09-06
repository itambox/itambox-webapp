from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from assets.models import Supplier
from core.purge_handlers import purge_object
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField


class CustomFieldDefinitionFoundationTests(TestCase):
    def test_generic_bulk_guard_detects_managed_definitions(self):
        from itambox.views.generic.bulk import _has_managed_definition_rows

        field = CustomField.objects.create(
            name="managed_bulk_field",
            label="Managed Bulk Field",
            activation=CustomField.ACTIVATION_GLOBAL,
            management_kind=CustomField.MANAGEMENT_CORE,
        )

        self.assertTrue(_has_managed_definition_rows(CustomField.objects.all(), CustomField))
        self.assertTrue(_has_managed_definition_rows([CustomField.objects.get(pk=field.pk)], CustomField))

    def test_optional_rfc1123_text_accepts_empty_value(self):
        from types import SimpleNamespace

        from extras.customfields import validate_custom_field_value

        definition = SimpleNamespace(
            field_type=CustomField.FIELD_TYPE_TEXT,
            regex=None,
            text_max_length=253,
            validation_rule="rfc1123_hostname",
            required=False,
        )

        self.assertEqual(validate_custom_field_value(definition, ""), "")

    def test_invalid_custom_field_regex_fails_closed(self):
        from types import SimpleNamespace

        from extras.customfields import validate_custom_field_value

        definition = SimpleNamespace(
            field_type=CustomField.FIELD_TYPE_TEXT,
            regex="[",
            text_max_length=None,
        )
        with self.assertRaises(ValidationError):
            validate_custom_field_value(definition, "value")

    def test_incompatible_inline_regex_flags_fail_closed(self):
        from types import SimpleNamespace

        from extras.customfields import validate_custom_field_value

        definition = SimpleNamespace(
            field_type=CustomField.FIELD_TYPE_TEXT,
            regex="(?u)^a$",
            text_max_length=None,
            validation_rule=None,
            nullable=False,
        )

        with self.assertRaises(ValidationError):
            validate_custom_field_value(definition, "a")

    def test_required_values_reject_type_specific_empty_values_but_accept_false(self):
        from types import SimpleNamespace

        from extras.customfields import validate_required_custom_field_values

        def definition(name, field_type):
            return SimpleNamespace(
                name=name,
                field_type=field_type,
                required=True,
                lifecycle=CustomField.LIFECYCLE_ACTIVE,
            )

        definitions = [
            definition("required_text", CustomField.FIELD_TYPE_TEXT),
            definition("required_multi", CustomField.FIELD_TYPE_MULTI_SELECT),
            definition("required_boolean", CustomField.FIELD_TYPE_BOOLEAN),
        ]
        with self.assertRaises(ValidationError) as raised:
            validate_required_custom_field_values(
                definitions,
                {"required_text": "", "required_multi": [], "required_boolean": False},
            )

        self.assertEqual(set(raised.exception.message_dict), {"required_text", "required_multi"})
        with self.assertRaises(ValidationError):
            validate_required_custom_field_values(
                [definition("typed_text", CustomField.FIELD_TYPE_TEXT)], {"typed_text": 123}
            )

    def test_required_single_select_rejects_empty_and_required_nullable_rejects_null(self):
        from types import SimpleNamespace

        from extras.customfields import validate_required_custom_field_values

        definition = SimpleNamespace(
            name="required_select",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            required=True,
            nullable=True,
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
        )
        with self.assertRaises(ValidationError):
            validate_required_custom_field_values([definition], {"required_select": ""})
        with self.assertRaises(ValidationError):
            validate_required_custom_field_values([definition], {"required_select": None})

    def test_definition_contract_rejects_unknown_validation_rule_at_model_boundary(self):
        field = CustomField(
            name="unknown_validation_rule",
            label="Unknown validation rule",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
            validation_rule="unknown-rule",
        )

        with self.assertRaises(ValidationError) as raised:
            field.full_clean()

        self.assertIn("validation_rule", raised.exception.message_dict)

    def test_reusable_definition_deprecation_retains_identity_and_blocks_delete(self):
        self.assertEqual(set(dict(CustomField.LIFECYCLE_CHOICES)), {"active", "deprecated"})
        self.assertFalse(hasattr(CustomField, "restore"))
        field = CustomField.objects.create(
            name="lifecycle_state_field",
            label="Lifecycle state field",
            activation=CustomField.ACTIVATION_GLOBAL,
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )

        with self.assertRaises(ProtectedError):
            field.delete()

        field.refresh_from_db()
        self.assertTrue(CustomField.objects.filter(pk=field.pk).exists())
        self.assertEqual(field.lifecycle, CustomField.LIFECYCLE_DEPRECATED)

    def test_overlapping_alternation_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a|b)+$")

    def test_nested_alternation_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^((a|b))+$")

    def test_nested_non_capturing_alternation_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(?:(?:a|b))+$")

    def test_omitted_lower_bound_nested_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a{,3})+$")

    def test_nested_optional_repeat_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a?)+$")

    def test_bounded_overlapping_alternation_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a|b){1,100000}$")

    def test_nested_bounded_repeat_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a{1,100000}){1,2}$")

    def test_adjacent_unbounded_repeats_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^a*a*a*a*a*a*a*a*a*a*b$")

    def test_oversized_regex_repeat_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a|b){1,999999999999999999999999}$")

    def test_group_wrapped_unbounded_repeats_regex_fail_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(a+)(a+)(a+)(a+)(a+)(a+)(a+)(a+)b$")

    def test_stable_definitions_and_ordered_membership_are_relational(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="port-speed",
            label="Port speed",
        )
        one_gigabit = CustomFieldChoice.objects.create(
            choice_set=choice_set,
            key="1g",
            label="1 Gbit/s",
            position=10,
        )
        field = CustomField.objects.create(
            name="port_speed",
            namespace="local",
            label="Port speed",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_COMPOSED,
            choice_set=choice_set,
            max_values=1,
        )
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="networking",
            label="Networking",
        )
        membership = CustomFieldsetField.objects.create(
            fieldset=fieldset,
            custom_field=field,
            position=10,
        )

        self.assertEqual(one_gigabit.key, "1g")
        self.assertEqual(list(fieldset.fields.all()), [field])
        self.assertEqual(membership.position, 10)

        field.name = "renamed_storage_key"
        with self.assertRaises(ValidationError):
            field.save()

        fieldset.slug = "renamed-networking"
        with self.assertRaises(ValidationError):
            fieldset.save()

        reserved_identity = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="reserved-identity",
            label="Reserved identity",
        )
        with self.assertRaises(ProtectedError):
            reserved_identity.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomFieldChoiceSet.objects.create(namespace="local", slug="reserved-identity", label="Reused identity")

    def test_required_generic_model_save_is_enforced(self):
        field = CustomField.objects.create(
            name="required_supplier_field",
            label="Required supplier field",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
            required=True,
        )
        field.object_types.add(ContentType.objects.get_for_model(Supplier))

        with self.assertRaises(ValidationError):
            Supplier.objects.create(name="Missing required value", slug="missing-required-value")

    def test_generic_model_save_rejects_invalid_active_field_values(self):
        supplier_type = ContentType.objects.get_for_model(Supplier)
        decimal_field = CustomField.objects.create(
            name="supplier_decimal_value",
            label="Supplier decimal value",
            field_type=CustomField.FIELD_TYPE_DECIMAL,
            activation=CustomField.ACTIVATION_GLOBAL,
            decimal_scale=2,
        )
        decimal_field.object_types.add(supplier_type)

        with self.assertRaises(ValidationError):
            Supplier.objects.create(
                name="Invalid decimal supplier",
                slug="invalid-decimal-supplier",
                custom_field_data={"supplier_decimal_value": "not-a-decimal"},
            )

        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="supplier-state-values",
            label="Supplier state values",
        )
        CustomFieldChoice.objects.create(choice_set=choice_set, key="active", label="Active", position=10)
        select_field = CustomField.objects.create(
            name="supplier_state_value",
            label="Supplier state value",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=choice_set,
            max_values=1,
        )
        select_field.object_types.add(supplier_type)

        with self.assertRaises(ValidationError):
            Supplier.objects.create(
                name="Invalid choice supplier",
                slug="invalid-choice-supplier",
                custom_field_data={"supplier_state_value": "removed"},
            )

    def test_deprecated_choice_set_rejects_new_choice_values(self):
        from extras.customfields import validate_custom_field_value

        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="deprecated-choice-set",
            label="Deprecated choice set",
        )
        choice = CustomFieldChoice.objects.create(choice_set=choice_set, key="one", label="One", position=10)
        field = CustomField.objects.create(
            name="deprecated_choice_field",
            label="Deprecated choice field",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=choice_set,
            max_values=1,
        )
        choice_set.lifecycle = CustomFieldChoiceSet.LIFECYCLE_DEPRECATED
        choice_set.save(update_fields=["lifecycle"])

        with self.assertRaises(ValidationError):
            validate_custom_field_value(field, choice.key)

    def test_multi_select_values_are_canonicalized_by_choice_position(self):
        from extras.customfields import validate_custom_field_value

        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="ordered-multi",
            label="Ordered multi",
        )
        CustomFieldChoice.objects.create(choice_set=choice_set, key="z-last", label="Last", position=20)
        CustomFieldChoice.objects.create(choice_set=choice_set, key="a-first", label="First", position=10)
        field = CustomField.objects.create(
            name="ordered_multi",
            label="Ordered multi",
            field_type=CustomField.FIELD_TYPE_MULTI_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=choice_set,
            max_values=2,
        )

        self.assertEqual(validate_custom_field_value(field, ["z-last", "a-first"]), ["a-first", "z-last"])

    def test_generic_purge_cannot_remove_permanent_schema_definition(self):
        field = CustomField.objects.create(
            name="permanent_schema_definition",
            label="Permanent schema definition",
            activation=CustomField.ACTIVATION_GLOBAL,
        )
        field.object_types.add(ContentType.objects.get(app_label="assets", model="asset"))

        with self.assertRaises(ProtectedError):
            purge_object(field)

        self.assertTrue(CustomField.objects.filter(pk=field.pk).exists())
        field.refresh_from_db()
        self.assertEqual(field.lifecycle, CustomField.LIFECYCLE_ACTIVE)

    def test_deprecated_definitions_retain_references_and_other_soft_delete_stays_intact(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="lifecycle-choices",
            label="Lifecycle choices",
        )
        choice = CustomFieldChoice.objects.create(
            choice_set=choice_set,
            key="enabled",
            label="Enabled",
            position=10,
        )
        field = CustomField.objects.create(
            name="lifecycle_state",
            namespace="local",
            label="Lifecycle state",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_COMPOSED,
            choice_set=choice_set,
            max_values=1,
        )
        field.object_types.add(ContentType.objects.get(app_label="assets", model="assettype"))
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="lifecycle",
            label="Lifecycle",
        )
        membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=10)

        from assets.models import AssetType, AssetTypeFieldset, Manufacturer

        manufacturer = Manufacturer.objects.create(name="Purge Manufacturer", slug="purge-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Purge Asset Type",
            slug="purge-asset-type",
        )
        asset_type_membership = AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)

        asset_type.delete()
        asset_type.refresh_from_db()
        self.assertIsNotNone(asset_type.deleted_at)
        self.assertTrue(AssetType.all_objects.filter(pk=asset_type.pk).exists())

        purge_object(asset_type)
        self.assertFalse(AssetType.all_objects.filter(pk=asset_type.pk).exists())
        self.assertFalse(AssetTypeFieldset.objects.filter(pk=asset_type_membership.pk).exists())

        for definition in (fieldset, field, choice_set):
            definition.lifecycle = definition.LIFECYCLE_DEPRECATED
            definition.save(update_fields=["lifecycle"])

        self.assertEqual(CustomFieldsetField.objects.get(pk=membership.pk).fieldset_id, fieldset.pk)
        self.assertEqual(CustomFieldsetField.objects.get(pk=membership.pk).custom_field_id, field.pk)
        self.assertEqual(CustomFieldChoice.objects.get(pk=choice.pk).choice_set_id, choice_set.pk)

        for definition in (fieldset, field, choice_set):
            with self.assertRaises(ProtectedError):
                definition.delete()
            definition.refresh_from_db()
            self.assertTrue(definition.__class__.objects.filter(pk=definition.pk).exists())
            self.assertEqual(definition.lifecycle, definition.LIFECYCLE_DEPRECATED)
