from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError, IntegrityError, models, transaction
from django.test import TestCase
from django.test.utils import override_settings

from assets.models.catalog import AssetType, AssetTypeFieldset, CategoryDefaultFieldset
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
)


@override_settings(ITAMBOX_ENV="dev")
class T06DefinitionSchemaTests(TestCase):
    def _field(self, name, *, activation=CustomField.ACTIVATION_GLOBAL, lifecycle=CustomField.LIFECYCLE_ACTIVE):
        field = CustomField.objects.create(
            name=name,
            namespace="local",
            label=name,
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=activation,
            lifecycle=lifecycle,
        )
        field.object_types.add(self.asset_type_content_type)
        return field

    @classmethod
    def setUpTestData(cls):
        from django.contrib.contenttypes.models import ContentType

        cls.asset_type_content_type = ContentType.objects.get_for_model(AssetType)

    def test_reusable_definitions_have_permanent_lifecycle_without_soft_delete_state(self):
        for model in (CustomField, CustomFieldset, CustomFieldChoiceSet, CustomFieldChoice):
            with self.subTest(model=model.__name__):
                with self.assertRaises(FieldDoesNotExist):
                    model._meta.get_field("deleted_at")
                self.assertFalse(hasattr(model, "restore"))
                self.assertNotIn("SoftDeleteManager", type(model._default_manager).__name__)

    def test_custom_field_has_required_explicit_activation_and_no_scope_property(self):
        activation = CustomField._meta.get_field("activation")

        self.assertFalse(activation.null)
        self.assertIs(activation.default, models.NOT_PROVIDED)
        with self.assertRaises(FieldDoesNotExist):
            CustomField._meta.get_field("scope")

    def test_ordering_position_constraints_are_deferred(self):
        for model, constraint_name in (
            (CustomFieldsetField, "unique_customfieldset_position"),
            (CustomFieldChoice, "unique_customfieldchoice_position"),
            (AssetTypeFieldset, "unique_assettype_fieldset_position"),
            (CategoryDefaultFieldset, "unique_category_default_position"),
        ):
            constraint = next(item for item in model._meta.constraints if item.name == constraint_name)
            with self.subTest(model=model.__name__):
                self.assertEqual(constraint.deferrable, models.Deferrable.DEFERRED)

    def test_global_field_cannot_join_a_fieldset(self):
        field = self._field("global_only")
        fieldset = CustomFieldset.objects.create(namespace="local", slug="global-only", label="Global only")

        with self.assertRaises((IntegrityError, DatabaseError)):
            CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

    def test_removing_last_membership_does_not_promote_composed_field(self):
        field = self._field("composed_field", activation=CustomField.ACTIVATION_COMPOSED)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="composed", label="Composed")
        membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

        membership.delete()
        field.refresh_from_db()

        self.assertEqual(field.activation, CustomField.ACTIVATION_COMPOSED)

    def test_global_activation_switch_with_membership_is_database_guarded(self):
        field = self._field("activation_guard", activation=CustomField.ACTIVATION_COMPOSED)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="activation-guard", label="Guard")
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)

        with self.assertRaises((IntegrityError, DatabaseError)):
            with transaction.atomic():
                CustomField.objects.filter(pk=field.pk).update(activation=CustomField.ACTIVATION_GLOBAL)

        field.refresh_from_db()
        self.assertEqual(field.activation, CustomField.ACTIVATION_COMPOSED)

    def test_queryset_delete_cannot_bypass_permanent_definition_guard(self):
        field = self._field("queryset_delete_guard")

        with self.assertRaises((IntegrityError, DatabaseError)):
            with transaction.atomic():
                CustomField.objects.filter(pk=field.pk).delete()

        self.assertTrue(CustomField.objects.filter(pk=field.pk).exists())

    def test_reusable_definition_identity_cannot_be_deleted(self):
        field = self._field("permanent_field")

        with self.assertRaises((IntegrityError, DatabaseError)):
            field.delete()

    def test_deprecated_choice_history_is_still_a_real_row(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="history",
            label="History",
            lifecycle=CustomFieldChoiceSet.LIFECYCLE_DEPRECATED,
        )
        choice = CustomFieldChoice.objects.create(
            choice_set=choice_set,
            key="retired",
            label="Retired",
            position=1,
            lifecycle=CustomFieldChoice.LIFECYCLE_DEPRECATED,
        )

        self.assertTrue(CustomFieldChoice.objects.filter(pk=choice.pk).exists())
        with self.assertRaises((IntegrityError, DatabaseError)):
            choice.delete()
