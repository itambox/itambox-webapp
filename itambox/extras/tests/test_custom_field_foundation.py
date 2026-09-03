from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

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
            validate_custom_field_regex(r"^((a|aa))+$")

    def test_nested_non_capturing_alternation_regex_fails_closed(self):
        from extras.customfields import validate_custom_field_regex

        with self.assertRaises(ValidationError):
            validate_custom_field_regex(r"^(?:(?:a|aa))+$")

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
        with self.assertRaises(ProtectedError), transaction.atomic():
            fieldset.delete(force_hard_delete=True)
        with self.assertRaises(ProtectedError), transaction.atomic():
            field.delete(force_hard_delete=True)
        with self.assertRaises(ProtectedError), transaction.atomic():
            choice_set.delete(force_hard_delete=True)
