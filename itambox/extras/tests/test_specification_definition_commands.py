from dataclasses import replace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from extras.services._definition_command_support import resource_revision_for_definition
from extras.services.definition_command_contracts import (
    CustomFieldChoiceCreateInputDTO,
    CustomFieldChoiceSetCreateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
    CustomFieldCreateInputDTO,
    CustomFieldsetCreateInputDTO,
    CustomFieldUpdateInputDTO,
)
from extras.services.definition_commands import (
    create_custom_field,
    create_custom_field_choice,
    create_custom_field_choice_set,
    create_custom_fieldset,
    deprecate_custom_field,
    deprecate_custom_field_choice,
    deprecate_custom_field_choice_set,
    replace_custom_fieldset_memberships,
    update_custom_field,
    update_custom_field_choice,
)
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor


class FieldDefinitionCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="definition-admin", password="test-password")
        permissions = Permission.objects.filter(
            content_type__app_label="extras",
            codename__in={
                "add_customfield",
                "change_customfield",
                "add_customfieldset",
                "change_customfieldset",
                "add_customfieldchoiceset",
                "change_customfieldchoiceset",
                "add_customfieldchoice",
                "change_customfieldchoice",
            },
        )
        self.user.user_permissions.add(*permissions)

    def actor(self):
        self.user.refresh_from_db()
        return ActorContextDTO(
            actor_id=self.user.pk,
            authentication_revision=authentication_revision_for_actor(self.user),
        )

    def field_input(self):
        return CustomFieldCreateInputDTO(
            namespace="acme",
            local_key="rack_owner",
            label="Rack owner",
            help_text="The operator-owned rack identifier.",
            field_type="text",
            activation="global",
            object_types=("assets.assettype",),
        )

    def composed_field_input(self, local_key="rack_owner", label="Rack owner"):
        return replace(
            self.field_input(),
            local_key=local_key,
            label=label,
            activation="composed",
        )

    def test_create_field_requires_global_permission_and_persists_qualified_key_and_targets(self):
        result = create_custom_field(actor=self.actor(), definition=self.field_input())

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.identity, "acme/acme__rack_owner")
        self.assertEqual(result.lifecycle, "active")
        self.assertTrue(result.resource_revision)

        from extras.models import CustomField

        field = CustomField.objects.get(pk=result.definition_id)
        self.assertEqual(field.name, "acme__rack_owner")
        self.assertEqual(field.namespace, "acme")
        self.assertEqual(list(field.object_types.values_list("app_label", "model")), [("assets", "assettype")])

    def test_staff_or_tenant_local_authority_without_global_model_permission_is_denied(self):
        from extras.models import CustomField

        self.user.user_permissions.clear()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        result = create_custom_field(actor=self.actor(), definition=self.field_input())

        self.assertEqual(result.outcome, "rejected")
        self.assertEqual([issue.code for issue in result.issues], ["OBJECT_UNAVAILABLE"])
        self.assertFalse(CustomField.objects.filter(name="acme__rack_owner").exists())

    def test_stale_update_rejects_without_timestamp_or_label_write(self):
        from extras.models import CustomField

        created = create_custom_field(actor=self.actor(), definition=self.field_input())
        field = CustomField.objects.get(pk=created.definition_id)
        old_updated_at = field.updated_at

        result = update_custom_field(
            actor=self.actor(),
            field_id=field.pk,
            expected_resource_revision="sha256:stale",
            changes=CustomFieldUpdateInputDTO(label="Changed label"),
        )

        self.assertEqual(result.outcome, "rejected")
        self.assertEqual([issue.code for issue in result.issues], ["STALE_RESOURCE"])
        field.refresh_from_db()
        self.assertEqual(field.label, "Rack owner")
        self.assertEqual(field.updated_at, old_updated_at)

    def test_deprecate_field_is_explicit_and_idempotent(self):
        from extras.models import CustomField

        created = create_custom_field(actor=self.actor(), definition=self.field_input())
        field = CustomField.objects.get(pk=created.definition_id)

        result = deprecate_custom_field(
            actor=self.actor(),
            field_id=field.pk,
            expected_resource_revision=created.resource_revision,
        )

        self.assertEqual(result.outcome, "changed")
        field.refresh_from_db()
        self.assertEqual(field.lifecycle, CustomField.LIFECYCLE_DEPRECATED)
        self.assertIsNotNone(field.deprecated_at)

        repeated = deprecate_custom_field(
            actor=self.actor(),
            field_id=field.pk,
            expected_resource_revision=result.resource_revision,
        )
        self.assertEqual(repeated.outcome, "no_op")

    def test_fieldset_creation_and_membership_replacement_preserve_order(self):
        from extras.models import CustomField, CustomFieldsetField

        first = create_custom_field(actor=self.actor(), definition=self.composed_field_input())
        second = create_custom_field(
            actor=self.actor(),
            definition=self.composed_field_input(local_key="rack_location", label="Rack location"),
        )
        fieldset = create_custom_fieldset(
            actor=self.actor(),
            definition=CustomFieldsetCreateInputDTO(
                namespace="acme",
                slug="hardware",
                label="Hardware",
                field_identities=(first.identity,),
            ),
        )

        self.assertEqual(fieldset.outcome, "created")
        self.assertEqual(
            list(
                CustomFieldsetField.objects.filter(fieldset_id=fieldset.definition_id).values_list(
                    "custom_field__name", "position"
                )
            ),
            [("acme__rack_owner", 1)],
        )

        replacement = replace_custom_fieldset_memberships(
            actor=self.actor(),
            fieldset_id=fieldset.definition_id,
            field_identities=(second.identity, first.identity),
            expected_resource_revision=fieldset.resource_revision,
        )

        self.assertEqual(replacement.outcome, "changed")
        self.assertEqual(
            list(
                CustomFieldsetField.objects.filter(fieldset_id=fieldset.definition_id)
                .order_by("position")
                .values_list("custom_field__name", "position")
            ),
            [("acme__rack_location", 1), ("acme__rack_owner", 2)],
        )
        self.assertGreater(replacement.version, fieldset.version)

        stale = replace_custom_fieldset_memberships(
            actor=self.actor(),
            fieldset_id=fieldset.definition_id,
            field_identities=(first.identity,),
            expected_resource_revision=fieldset.resource_revision,
        )
        self.assertEqual(stale.outcome, "rejected")
        self.assertEqual([issue.code for issue in stale.issues], ["STALE_RESOURCE"])
        self.assertEqual(
            list(
                CustomField.objects.filter(pk__in=[first.definition_id, second.definition_id])
                .order_by("name")
                .values_list("name", flat=True)
            ),
            ["acme__rack_location", "acme__rack_owner"],
        )

    def test_choice_set_and_choice_lifecycle_use_global_permissions_and_revisions(self):
        from extras.models import CustomFieldChoice, CustomFieldChoiceSet

        choice_set = create_custom_field_choice_set(
            actor=self.actor(),
            definition=CustomFieldChoiceSetCreateInputDTO(
                namespace="acme",
                slug="ownership",
                label="Ownership",
            ),
        )
        self.assertEqual(choice_set.outcome, "created")

        choice = create_custom_field_choice(
            actor=self.actor(),
            definition=CustomFieldChoiceCreateInputDTO(
                choice_set_id=choice_set.definition_id,
                key="operator",
                label="Operator owned",
                position=1,
            ),
        )
        self.assertEqual(choice.outcome, "created")
        self.assertEqual(choice.identity, "acme/ownership#operator")
        self.assertEqual(CustomFieldChoice.objects.get(pk=choice.definition_id).key, "operator")

        updated_choice = update_custom_field_choice(
            actor=self.actor(),
            choice_id=choice.definition_id,
            expected_resource_revision=choice.resource_revision,
            changes=CustomFieldChoiceUpdateInputDTO(label="Operator-managed"),
        )
        self.assertEqual(updated_choice.outcome, "changed")
        deprecated_choice = deprecate_custom_field_choice(
            actor=self.actor(),
            choice_id=choice.definition_id,
            expected_resource_revision=updated_choice.resource_revision,
        )
        self.assertEqual(deprecated_choice.outcome, "changed")

        choice_set_row = CustomFieldChoiceSet.objects.get(pk=choice_set.definition_id)
        current_choice_set_revision = resource_revision_for_definition(choice_set_row)
        retired = deprecate_custom_field_choice_set(
            actor=self.actor(),
            choice_set_id=choice_set_row.pk,
            expected_resource_revision=current_choice_set_revision,
        )
        self.assertEqual(retired.outcome, "changed")
        choice_set_row.refresh_from_db()
        self.assertEqual(choice_set_row.lifecycle, CustomFieldChoiceSet.LIFECYCLE_DEPRECATED)

    def test_choice_set_deprecation_rejects_active_select_field_dependency(self):
        choice_set = create_custom_field_choice_set(
            actor=self.actor(),
            definition=CustomFieldChoiceSetCreateInputDTO(
                namespace="acme",
                slug="status",
                label="Status",
            ),
        )
        create_custom_field_choice(
            actor=self.actor(),
            definition=CustomFieldChoiceCreateInputDTO(
                choice_set_id=choice_set.definition_id,
                key="active",
                label="Active",
                position=1,
            ),
        )
        field = create_custom_field(
            actor=self.actor(),
            definition=replace(
                self.field_input(),
                local_key="status",
                label="Status",
                field_type="single-select",
                max_values=1,
                choice_set_id=choice_set.definition_id,
            ),
        )
        self.assertEqual(field.outcome, "created")

        from extras.models import CustomFieldChoiceSet

        current_choice_set_revision = resource_revision_for_definition(
            CustomFieldChoiceSet.objects.get(pk=choice_set.definition_id)
        )
        rejected = deprecate_custom_field_choice_set(
            actor=self.actor(),
            choice_set_id=choice_set.definition_id,
            expected_resource_revision=current_choice_set_revision,
        )

        self.assertEqual(rejected.outcome, "rejected")
        self.assertEqual([issue.code for issue in rejected.issues], ["DEPENDENCY_RETIREMENT"])
        from extras.models import CustomFieldChoiceSet

        self.assertEqual(
            CustomFieldChoiceSet.objects.get(pk=choice_set.definition_id).lifecycle,
            CustomFieldChoiceSet.LIFECYCLE_ACTIVE,
        )
