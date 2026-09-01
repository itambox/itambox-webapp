from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField


class CustomFieldDefinitionFoundationTests(TestCase):
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
