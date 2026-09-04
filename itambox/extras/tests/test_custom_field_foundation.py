from datetime import timedelta
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core import management
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from assets.models import Supplier
from core.purge_handlers import TombstonePurgeBlocked, purge_object
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField


class CustomFieldDefinitionFoundationTests(TestCase):
    def test_generic_bulk_guard_detects_managed_definitions(self):
        from itambox.views.generic.bulk import _has_managed_definition_rows

        field = CustomField.objects.create(
            name="managed_bulk_field",
            label="Managed Bulk Field",
            management_kind=CustomField.MANAGEMENT_CORE,
        )

        self.assertTrue(_has_managed_definition_rows(CustomField.objects.all(), CustomField))
        self.assertTrue(_has_managed_definition_rows([CustomField.objects.get(pk=field.pk)], CustomField))

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
                deleted_at=None,
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
            deleted_at=None,
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
            scope=CustomField.SCOPE_ASSET,
            validation_rule="unknown-rule",
        )

        with self.assertRaises(ValidationError) as raised:
            field.full_clean()

        self.assertIn("validation_rule", raised.exception.message_dict)

    def test_managed_definition_lifecycle_uses_deleted_at_for_delete_state(self):
        self.assertNotIn("deleted", dict(CustomField.LIFECYCLE_CHOICES))
        field = CustomField.objects.create(
            name="lifecycle_state_field",
            label="Lifecycle state field",
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
        )

        field.delete()
        field.refresh_from_db()
        self.assertIsNotNone(field.deleted_at)
        self.assertEqual(field.lifecycle, CustomField.LIFECYCLE_ACTIVE)

        field.restore()
        field.refresh_from_db()
        self.assertIsNone(field.deleted_at)
        self.assertEqual(field.lifecycle, CustomField.LIFECYCLE_ACTIVE)

    def test_legacy_deleted_lifecycle_is_normalized_on_restore(self):
        field = CustomField.objects.create(
            name="legacy_deleted_lifecycle",
            label="Legacy deleted lifecycle",
        )
        CustomField.all_objects.filter(pk=field.pk).update(lifecycle="deleted", deleted_at=timezone.now())

        field.refresh_from_db()
        field.restore()
        field.refresh_from_db()
        self.assertIsNone(field.deleted_at)
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
            scope=CustomField.SCOPE_ASSET_TYPE,
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

        tombstone = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="reserved-identity",
            label="Reserved identity",
        )
        tombstone.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomFieldChoiceSet.objects.create(namespace="local", slug="reserved-identity", label="Reused identity")

    def test_required_generic_model_save_is_enforced(self):
        field = CustomField.objects.create(
            name="required_supplier_field",
            label="Required supplier field",
            field_type=CustomField.FIELD_TYPE_TEXT,
            required=True,
        )
        field.object_types.add(ContentType.objects.get_for_model(Supplier))

        with self.assertRaises(ValidationError):
            Supplier.objects.create(name="Missing required value", slug="missing-required-value")

    def test_deleted_choice_set_rejects_new_choice_values(self):
        from extras.customfields import validate_custom_field_value

        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="deleted-choice-set",
            label="Deleted choice set",
        )
        choice = CustomFieldChoice.objects.create(choice_set=choice_set, key="one", label="One", position=10)
        field = CustomField.objects.create(
            name="deleted_choice_field",
            label="Deleted choice field",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            choice_set=choice_set,
            max_values=1,
        )
        choice_set.delete()

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
            choice_set=choice_set,
            max_values=2,
        )

        self.assertEqual(validate_custom_field_value(field, ["z-last", "a-first"]), ["a-first", "z-last"])

    def test_generic_purge_preserves_schema_definition_tombstones(self):
        field = CustomField.objects.create(
            name="permanent_schema_tombstone",
            label="Permanent schema tombstone",
            scope=CustomField.SCOPE_ASSET,
        )
        field.object_types.add(ContentType.objects.get(app_label="assets", model="asset"))
        field.delete()
        old_deleted_at = timezone.now() - timedelta(days=31)
        CustomField.all_objects.filter(pk=field.pk).update(deleted_at=old_deleted_at)
        output = StringIO()

        management.call_command("purge_deleted", days=30, stdout=output)

        self.assertTrue(CustomField.all_objects.filter(pk=field.pk).exists())
        management.call_command("purge_deleted", days=30, stdout=output, dry_run=True)

        self.assertIn("Total permanent tombstones deferred: 1", output.getvalue())

    def test_soft_delete_preserves_references_and_restore_keeps_composition(self):
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
            scope=CustomField.SCOPE_ASSET_TYPE,
            choice_set=choice_set,
            max_values=1,
        )
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="lifecycle",
            label="Lifecycle",
        )
        membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=10)

        fieldset.delete()
        fieldset.refresh_from_db()
        self.assertIsNotNone(fieldset.deleted_at)
        self.assertEqual(CustomFieldsetField.objects.get(pk=membership.pk).fieldset_id, fieldset.pk)
        fieldset.restore()

        field.delete()
        field.refresh_from_db()
        self.assertIsNotNone(field.deleted_at)
        self.assertEqual(CustomFieldsetField.objects.get(pk=membership.pk).custom_field_id, field.pk)
        field.restore()

        choice_set.delete()
        choice_set.refresh_from_db()
        self.assertIsNotNone(choice_set.deleted_at)
        self.assertEqual(CustomFieldChoice.all_objects.get(pk=choice.pk).choice_set_id, choice_set.pk)
        choice_set.restore()

        self.assertIsNone(fieldset.deleted_at)
        self.assertIsNone(field.deleted_at)
        self.assertIsNone(choice_set.deleted_at)

        from assets.models import AssetType, AssetTypeFieldset, Manufacturer

        manufacturer = Manufacturer.objects.create(name="Purge Manufacturer", slug="purge-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Purge Asset Type",
            slug="purge-asset-type",
        )
        asset_type_membership = AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)

        purge_object(asset_type)
        self.assertFalse(AssetTypeFieldset.objects.filter(pk=asset_type_membership.pk).exists())

        fieldset.delete()
        field.delete()
        choice_set.delete()
        for definition in (fieldset, field, choice_set):
            with self.assertRaises(TombstonePurgeBlocked):
                purge_object(definition)
            self.assertTrue(definition.__class__.all_objects.filter(pk=definition.pk).exists())
