"""PostgreSQL-backed regressions for the Category-default apply preview and commands."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models.asset import Asset
from assets.models.catalog import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from assets.services.specifications._command_support import load_prospective_definition, resource_revision_for_owner
from assets.services.specifications.commands import apply_category_defaults, preview_apply_category_defaults
from assets.services.specifications.contracts import (
    AssetTypePreviewDTO,
    CommandRejectedDTO,
    OwnerChangedDTO,
    OwnerNoOpDTO,
    SpecificationPatchDTO,
)
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldset, CustomFieldsetField, Event
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()


class SpecificationApplyDefaultsCommandTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apply-defaults-editor")
        self.manufacturer = Manufacturer.objects.create(name="Apply maker", slug="apply-maker")
        self.category = Category.objects.create(name="Apply category", slug="apply-category")
        self.first_field = self._field("first_note")
        self.second_field = self._field("second_note")
        self.required_field = self._field("required_note", required=True)
        self.asset_field = self._field("asset_note", target=Asset)

        self.first = self._fieldset("first", (self.first_field,))
        self.second = self._fieldset("second", (self.second_field,))
        self.required = self._fieldset("required", (self.required_field,))
        self.asset_only = self._fieldset("asset-only", (self.asset_field,))
        self.global_field = CustomField.objects.create(
            name="apply_global_note",
            namespace="local",
            label="Apply global note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.global_field.object_types.add(ContentType.objects.get_for_model(AssetType))

        self.type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Apply type",
            slug="apply-type",
            category=self.category,
        )
        AssetTypeFieldset.objects.create(asset_type=self.type, fieldset=self.first, position=1)
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.first, position=1)

        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(AssetType),
                codename="change_assettype",
            )
        )

    def _field(self, name, *, target=AssetType, required=False):
        field = CustomField.objects.create(
            name=name,
            namespace="local",
            label=name.replace("_", " ").title(),
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            required=required,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        field.object_types.add(ContentType.objects.get_for_model(target))
        return field

    def _fieldset(self, slug, fields):
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug=slug,
            label=slug.replace("-", " ").title(),
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        for position, field in enumerate(fields, start=1):
            CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=position)
        return fieldset

    def _actor(self):
        return ActorContextDTO(
            actor_id=self.user.pk,
            authentication_revision=authentication_revision_for_actor(self.user),
        )

    def _resource_revision(self):
        owner = AssetType.all_objects.get(pk=self.type.pk)
        return resource_revision_for_owner(owner)

    def _changes(self, model, pk):
        return ObjectChange._base_manager.filter(
            changed_object_type=ContentType.objects.get_for_model(model),
            changed_object_id=pk,
        )

    def _preview(self, patch=None):
        result = preview_apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            expected_resource_revision=self._resource_revision(),
            patch=patch or SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, AssetTypePreviewDTO)
        return result

    def test_apply_defaults_persists_category_memberships_on_existing_type(self):
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.second, position=2)
        preview = self._preview(SpecificationPatchDTO(set_values={"second_note": "applied"}, clear_keys=()))
        self.assertTrue(preview.consumes_category_defaults)
        self.assertIsNotNone(preview.preview_token)
        self.assertIsNotNone(preview.expected_category_default_snapshot_revision)
        self.assertEqual(preview.expected_resource_revision, self._resource_revision())
        self.assertEqual(preview.issues, ())

        before_changes = self._changes(AssetType, self.type.pk).count()
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={"second_note": "applied"}, clear_keys=()),
        )
        self.assertIsInstance(result, OwnerChangedDTO)
        self.assertEqual(result.owner.owner_kind, "asset_type")
        self.assertEqual(result.owner.owner_id, self.type.pk)
        self.assertEqual(result.definition_revision, preview.expected_definition_revision)
        self.type.refresh_from_db()
        self.assertEqual(
            list(self.type.fieldset_memberships.values_list("fieldset_id", "position")),
            [(self.first.pk, 1), (self.second.pk, 2)],
        )
        self.assertEqual(self.type.custom_field_data, {"second_note": "applied"})
        self.assertEqual(self._changes(AssetType, self.type.pk).count(), before_changes + 1)

    def test_apply_defaults_no_op_when_memberships_and_values_unchanged(self):
        preview = self._preview()
        membership_ids = list(
            AssetTypeFieldset.objects.filter(asset_type=self.type).order_by("position").values_list("pk", flat=True)
        )
        self.type.refresh_from_db()
        before_timestamp = self.type.updated_at
        before_changes = self._changes(AssetType, self.type.pk).count()
        before_events = Event.objects.filter(
            object_id=self.type.pk,
            model=ContentType.objects.get_for_model(AssetType),
        ).count()

        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, OwnerNoOpDTO)
        self.type.refresh_from_db()
        self.assertEqual(
            list(
                AssetTypeFieldset.objects.filter(asset_type=self.type).order_by("position").values_list("pk", flat=True)
            ),
            membership_ids,
        )
        self.assertEqual(self.type.updated_at, before_timestamp)
        self.assertEqual(self._changes(AssetType, self.type.pk).count(), before_changes)
        self.assertEqual(
            Event.objects.filter(
                object_id=self.type.pk,
                model=ContentType.objects.get_for_model(AssetType),
            ).count(),
            before_events,
        )

    def test_apply_defaults_missing_token_is_missing_precondition(self):
        preview = self._preview()
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=None,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("MISSING_PRECONDITION", ("preview_token",))],
        )

    def test_apply_defaults_all_missing_preconditions_are_ordered(self):
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=None,
            expected_resource_revision=None,
            expected_definition_revision=None,
            expected_category_default_snapshot_revision=None,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [
                ("MISSING_PRECONDITION", ("preview_token",)),
                ("MISSING_PRECONDITION", ("expected_resource_revision",)),
                ("MISSING_PRECONDITION", ("expected_definition_revision",)),
                ("MISSING_PRECONDITION", ("expected_category_default_snapshot_revision",)),
            ],
        )

    def test_apply_defaults_stale_resource_wins_before_stale_definition(self):
        preview = self._preview()
        AssetType._base_manager.filter(pk=self.type.pk).update(model="Apply type changed")
        CustomField.objects.filter(pk=self.global_field.pk).update(label="Changed after plan")

        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("STALE_RESOURCE", ("expected_resource_revision",))],
        )

    def test_apply_defaults_stale_definition_fires_after_resource_matches(self):
        preview = self._preview()
        before_memberships = list(
            AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")
        )
        CustomField.objects.filter(pk=self.global_field.pk).update(label="Changed after plan")

        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_DEFINITION"])
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")),
            before_memberships,
        )

    def test_apply_defaults_preview_rejects_stale_expected_resource_revision(self):
        resource_revision = self._resource_revision()
        AssetType._base_manager.filter(pk=self.type.pk).update(model="Apply type changed")
        result = preview_apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            expected_resource_revision=resource_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_RESOURCE"])

    def test_apply_defaults_requires_change_permission_and_hides_missing_type(self):
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=999999,
            preview_token="any",
            expected_resource_revision="sha256:any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision="sha256:any",
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual(result.issues[0].code, "OBJECT_UNAVAILABLE")

        self.user.user_permissions.clear()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        denied = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token="any",
            expected_resource_revision="sha256:any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision="sha256:any",
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(denied, CommandRejectedDTO)
        self.assertIsNone(denied.safe_owner)
        self.assertEqual(denied.issues[0].code, "OBJECT_UNAVAILABLE")

        preview = preview_apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            expected_resource_revision=self._resource_revision(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

    def test_apply_defaults_recomputed_snapshot_binds_definition_unchanged_case(self):
        preview = self._preview()
        position = CategoryDefaultFieldset.objects.get(category=self.category, fieldset=self.first).position
        CategoryDefaultFieldset.objects.filter(category=self.category, fieldset=self.first).update(
            position=position + 7
        )
        definition, _definitions, _graph = load_prospective_definition(
            ("local/first",),
            "asset_type",
            (),
        )
        self.assertEqual(definition.revision, preview.expected_definition_revision)

        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("STALE_RESOURCE", ("expected_category_default_snapshot_revision",))],
        )
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")),
            [(self.first.pk, 1)],
        )

    def test_apply_defaults_without_category_is_unavailable(self):
        AssetType._base_manager.filter(pk=self.type.pk).update(category_id=None)
        preview = preview_apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            expected_resource_revision=self._resource_revision(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token="any",
            expected_resource_revision="sha256:any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision="sha256:any",
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual(result.issues[0].code, "OBJECT_UNAVAILABLE")

    def test_apply_defaults_required_field_from_defaults_is_validated_without_writes(self):
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.required, position=2)
        preview = self._preview()
        self.assertIn("REQUIRED_FIELD", [issue.code for issue in preview.issues])

        before_memberships = list(
            AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")
        )
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIn("REQUIRED_FIELD", [issue.code for issue in result.issues])
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")),
            before_memberships,
        )

    def test_apply_defaults_token_from_different_preview_is_stale_plan(self):
        first = self._preview(SpecificationPatchDTO(set_values={"first_note": "first"}, clear_keys=()))
        second = self._preview(SpecificationPatchDTO(set_values={"first_note": "second"}, clear_keys=()))
        result = apply_category_defaults(
            actor=self._actor(),
            asset_type_id=self.type.pk,
            preview_token=first.preview_token,
            expected_resource_revision=second.expected_resource_revision,
            expected_definition_revision=second.expected_definition_revision,
            expected_category_default_snapshot_revision=second.expected_category_default_snapshot_revision,
            patch=SpecificationPatchDTO(set_values={"first_note": "second"}, clear_keys=()),
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_PLAN"])

    def test_apply_defaults_save_failure_rolls_back_memberships_and_audit(self):
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.second, position=2)
        preview = self._preview()
        before_memberships = list(
            AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")
        )
        before_changes = self._changes(AssetType, self.type.pk).count()

        def failing_save(_owner, *args, **kwargs):
            del args, kwargs
            raise ValidationError({"asset": "forced failure"})

        with patch.object(AssetType, "save", new=failing_save):
            result = apply_category_defaults(
                actor=self._actor(),
                asset_type_id=self.type.pk,
                preview_token=preview.preview_token,
                expected_resource_revision=preview.expected_resource_revision,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=self.type).values_list("fieldset_id", "position")),
            before_memberships,
        )
        self.assertEqual(self._changes(AssetType, self.type.pk).count(), before_changes)
