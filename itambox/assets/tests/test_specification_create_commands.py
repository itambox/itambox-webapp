"""PostgreSQL-backed regressions for the Type-create preview and write commands."""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models.asset import Asset
from assets.models.catalog import (
    AssetRole,
    AssetType,
    AssetTypeFieldset,
    Category,
    CategoryDefaultFieldset,
    Depreciation,
    Manufacturer,
)
from assets.services.specifications._create_commands import _create_input_digest
from assets.services.specifications.commands import create_asset_type, preview_asset_type_create
from assets.services.specifications.contracts import (
    AssetTypeNativeCreateInputDTO,
    AssetTypePreviewDTO,
    CommandRejectedDTO,
    FieldsetSelectionDTO,
    OwnerCreatedDTO,
    SpecificationPatchDTO,
)
from assets.services.specifications.preview_tokens import (
    PreviewTokenExpectation,
    issue_preview_token,
)
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldset, CustomFieldsetField, Tag
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()


class SpecificationCreateCommandTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="create-editor")
        self.manufacturer = Manufacturer.objects.create(name="Create maker", slug="create-maker")
        self.category = Category.objects.create(name="Create category", slug="create-category")
        self.role = AssetRole.objects.create(name="Create role", slug="create-role")
        self.depreciation = Depreciation.objects.create(name="Create depreciation", months=36)

        self.first_field = self._field("first_note")
        self.shared_field = self._field("shared_note")
        self.second_field = self._field("second_note")
        self.required_field = self._field("required_note", required=True)
        self.asset_field = self._field("asset_note", target=Asset)

        self.first = self._fieldset("first", (self.first_field, self.shared_field))
        self.second = self._fieldset("second", (self.second_field, self.shared_field))
        self.required = self._fieldset("required", (self.required_field,))
        self.asset_only = self._fieldset("asset-only", (self.asset_field,))
        self.deprecated = CustomFieldset.objects.create(
            namespace="local",
            slug="deprecated",
            label="Deprecated",
            lifecycle=CustomFieldset.LIFECYCLE_DEPRECATED,
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        self.global_field = CustomField.objects.create(
            name="global_note",
            namespace="local",
            label="Global note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.global_field.object_types.add(ContentType.objects.get_for_model(AssetType))

        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.first, position=1)
        self.existing = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Existing model",
            slug="create-maker-existing-model",
        )
        self.tag = Tag.objects.create(name="Create tag", slug="create-tag")

        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(AssetType),
                codename="add_assettype",
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

    def _native(
        self,
        *,
        category_id=None,
        manufacturer_id=None,
        model=None,
        slug=None,
        staged_image_id=None,
        tag_ids=(),
    ):
        return AssetTypeNativeCreateInputDTO(
            manufacturer_id=manufacturer_id if manufacturer_id is not None else self.manufacturer.pk,
            model=model or "Create model",
            slug=slug,
            part_number="PN-1",
            ean="4000000000001",
            region="EU",
            configuration="64GB",
            eol_months=48,
            category_id=category_id,
            suggested_asset_role_id=self.role.pk,
            depreciation_id=self.depreciation.pk,
            staged_image_id=staged_image_id,
            description="Create description",
            comments="Create comments",
            tag_ids=tag_ids,
            requestable=True,
        )

    def _actor(self):
        return ActorContextDTO(
            actor_id=self.user.pk,
            authentication_revision=authentication_revision_for_actor(self.user),
        )

    def _omitted(self):
        return FieldsetSelectionDTO("omitted", ())

    def _explicit_empty(self):
        return FieldsetSelectionDTO("explicit", ())

    def _selection(self, *fieldsets):
        return FieldsetSelectionDTO(
            "explicit",
            tuple(f"{fieldset.namespace}/{fieldset.slug}" for fieldset in fieldsets),
        )

    def _changes(self, model, pk):
        return ObjectChange._base_manager.filter(
            changed_object_type=ContentType.objects.get_for_model(model),
            changed_object_id=pk,
        )

    def test_fieldsets_selection_presence_invariants(self):
        self.assertEqual(self._omitted().identities, ())
        self.assertEqual(self._explicit_empty().identities, ())
        with self.assertRaises(FrozenInstanceError):
            self._omitted().identities = ("local/first",)
        with self.assertRaises(TypeError):
            FieldsetSelectionDTO("explicit", None)
        with self.assertRaises(TypeError):
            FieldsetSelectionDTO("explicit", [])
        with self.assertRaises(ValueError):
            FieldsetSelectionDTO("omitted", ("local/first",))
        with self.assertRaises(ValueError):
            FieldsetSelectionDTO("absent", ())
        with self.assertRaises(ValueError):
            FieldsetSelectionDTO("explicit", ("local/first", "local/first"))
        with self.assertRaises(ValueError):
            FieldsetSelectionDTO("explicit", ("not-qualified",))

    def test_native_input_dto_rejects_malformed_values(self):
        with self.assertRaises(ValueError):
            self._native(manufacturer_id=0)
        with self.assertRaises(ValueError):
            AssetTypeNativeCreateInputDTO(
                manufacturer_id=1,
                model="",
                slug=None,
                part_number="",
                ean="",
                region="",
                configuration="",
                eol_months=None,
                category_id=None,
                suggested_asset_role_id=None,
                depreciation_id=None,
                staged_image_id=None,
                description="",
                comments="",
                tag_ids=(),
                requestable=False,
            )
        with self.assertRaises(TypeError):
            self._native(tag_ids=None)
        with self.assertRaises(ValueError):
            self._native(tag_ids=(0,))
        with self.assertRaises(ValueError):
            self._native(staged_image_id="")

    def test_create_with_category_and_omitted_fieldsets_consumes_defaults(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        self.assertTrue(preview.consumes_category_defaults)
        self.assertIsNotNone(preview.preview_token)
        self.assertIsNotNone(preview.expected_category_default_snapshot_revision)
        self.assertIsNone(preview.expected_resource_revision)
        self.assertEqual(preview.issues, ())

        before_count = AssetType.all_objects.count()
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        self.assertEqual(AssetType.all_objects.count(), before_count + 1)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertEqual(result.owner.owner_kind, "asset_type")
        self.assertEqual(
            list(created.fieldset_memberships.values_list("fieldset_id", "position")),
            [(self.first.pk, 1)],
        )
        self.assertEqual(created.custom_field_data, {"first_note": "typed"})
        self.assertEqual(created.category_id, self.category.pk)
        self.assertEqual(result.definition_revision, preview.expected_definition_revision)
        changes = self._changes(AssetType, created.pk)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(changes.first().user, self.user)

    def test_create_missing_preconditions_emit_two_ordered_missing_issues_without_state_lookup(self):
        before_count = AssetType.all_objects.count()
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=999999, manufacturer_id=999999),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision="sha256:unused",
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [
                ("MISSING_PRECONDITION", ("preview_token",)),
                ("MISSING_PRECONDITION", ("expected_category_default_snapshot_revision",)),
            ],
        )
        self.assertEqual(AssetType.all_objects.count(), before_count)

    def test_create_missing_single_precondition_emits_only_its_row(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("MISSING_PRECONDITION", ("preview_token",))],
        )

    def test_create_explicit_empty_fieldsets_requires_no_token_and_no_default_memberships(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        self.assertFalse(preview.consumes_category_defaults)
        self.assertIsNone(preview.preview_token)
        self.assertIsNone(preview.expected_category_default_snapshot_revision)

        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertEqual(created.fieldset_memberships.count(), 0)

    def test_create_without_category_requires_no_token(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        self.assertFalse(preview.consumes_category_defaults)
        self.assertIsNone(preview.preview_token)

        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertIsNone(created.category_id)
        self.assertEqual(created.fieldset_memberships.count(), 0)

    def test_stale_category_default_snapshot_is_stale_resource_before_write(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        before_count = AssetType.all_objects.count()
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.second, position=2)

        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("STALE_RESOURCE", ("expected_category_default_snapshot_revision",))],
        )
        self.assertEqual(AssetType.all_objects.count(), before_count)

    def test_stale_definition_is_rejected_after_locked_reload(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        CustomField.objects.filter(pk=self.global_field.pk).update(label="Changed after plan")

        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_DEFINITION"])
        self.assertEqual(AssetType.all_objects.count(), 1)

    def test_token_claim_or_format_failures_are_stale_plan(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)

        digest_mismatch = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "different"}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(digest_mismatch, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in digest_mismatch.issues], ["STALE_PLAN"])

        malformed = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
            preview_token="not-a-signed-token",
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(malformed, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in malformed.issues], ["STALE_PLAN"])

        expired = issue_preview_token(
            PreviewTokenExpectation(
                actor_id=self.user.pk,
                authentication_revision=authentication_revision_for_actor(self.user),
                access_scope_fingerprint=None,
                command_kind="create_asset_type",
                target=None,
                normalized_input_digest=_create_input_digest(
                    self._native(category_id=self.category.pk),
                    self._omitted(),
                    SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
                ),
                expected_resource_revision=None,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
                historical_state_digest=None,
            ),
            key=settings.SECRET_KEY,
            now=int(time.time()) - 30 * 60 - 1,
        )
        expired_result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={"first_note": "typed"}, clear_keys=()),
            preview_token=expired,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
        )
        self.assertIsInstance(expired_result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in expired_result.issues], ["STALE_PLAN"])
        self.assertEqual(AssetType.all_objects.count(), 1)

    def test_preview_rejects_unknown_deprecated_or_inapplicable_references(self):
        for selection in (
            FieldsetSelectionDTO("explicit", ("local/does-not-exist",)),
            self._selection(self.deprecated),
            self._selection(self.asset_only),
        ):
            result = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk),
                fieldsets=selection,
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])

    def test_preview_returns_prospective_definition_and_same_input_verifies_at_write(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._selection(self.first, self.second),
            patch=SpecificationPatchDTO(set_values={"first_note": "plan"}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        self.assertFalse(preview.consumes_category_defaults)
        self.assertIsNotNone(preview.definition)
        self.assertEqual(preview.definition.revision, preview.expected_definition_revision)
        self.assertEqual(
            tuple(str(item.fieldset_identity) for item in preview.definition.persisted_memberships),
            ("local/first", "local/second"),
        )
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._selection(self.first, self.second),
            patch=SpecificationPatchDTO(set_values={"first_note": "plan"}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertEqual(
            list(created.fieldset_memberships.values_list("fieldset_id", "position")),
            [(self.first.pk, 1), (self.second.pk, 2)],
        )
        self.assertEqual(created.custom_field_data, {"first_note": "plan"})

    def test_preview_and_create_require_global_add_permission(self):
        self.user.user_permissions.clear()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertIsNone(preview.safe_owner)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

        create = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token="any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision="sha256:any",
        )
        self.assertIsInstance(create, CommandRejectedDTO)
        self.assertIsNone(create.safe_owner)
        self.assertEqual(create.issues[0].code, "OBJECT_UNAVAILABLE")

    def test_inactive_actor_is_denied_even_with_global_permission(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

    def test_missing_manufacturer_or_category_is_nondisclosing(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=999999),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertIsNone(preview.safe_owner)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(manufacturer_id=999999),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(preview.issues[0].code, "OBJECT_UNAVAILABLE")

        create = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=999999),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token="any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision="sha256:any",
        )
        self.assertIsInstance(create, CommandRejectedDTO)
        self.assertIsNone(create.safe_owner)
        self.assertEqual(create.issues[0].code, "OBJECT_UNAVAILABLE")

    def test_native_fields_and_slug_generation_are_persisted(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, slug=None, tag_ids=(self.tag.pk,)),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, slug=None, tag_ids=(self.tag.pk,)),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertEqual(created.slug, "create-maker-create-model")
        self.assertEqual(created.model, "Create model")
        self.assertEqual(created.part_number, "PN-1")
        self.assertEqual(created.ean, "4000000000001")
        self.assertEqual(created.region, "EU")
        self.assertEqual(created.configuration, "64GB")
        self.assertEqual(created.eol_months, 48)
        self.assertEqual(created.asset_role_id, self.role.pk)
        self.assertEqual(created.depreciation_id, self.depreciation.pk)
        self.assertEqual(created.description, "Create description")
        self.assertEqual(created.comments, "Create comments")
        self.assertTrue(created.requestable)
        self.assertEqual(list(created.tags.values_list("pk", flat=True)), [self.tag.pk])

    def test_duplicate_explicit_slug_is_reference_conflict_and_auto_slug_stays_unique(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, slug="create-maker-existing-model"),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        before_count = AssetType.all_objects.count()
        before_changes = self._changes(AssetType, self.existing.pk).count()
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, slug="create-maker-existing-model"),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.assertEqual(AssetType.all_objects.count(), before_count)
        self.assertEqual(self._changes(AssetType, self.existing.pk).count(), before_changes)

        auto = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, model="Existing model", slug=None),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(auto, AssetTypePreviewDTO)
        auto_result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, model="Existing model", slug=None),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=auto.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(auto_result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=auto_result.owner.owner_id)
        self.assertEqual(created.slug, "create-maker-existing-model-1")

    def test_tag_and_membership_writes_are_atomic_with_owner_create(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk, tag_ids=(self.tag.pk,)),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        before_count = AssetType.all_objects.count()

        def failing_save(_owner, *args, **kwargs):
            del args, kwargs
            raise ValidationError({"model": "forced failure"})

        with patch.object(AssetType, "save", new=failing_save):
            result = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, tag_ids=(self.tag.pk,)),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=preview.preview_token,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.assertEqual(AssetType.all_objects.count(), before_count)

    def test_unknown_tag_reference_is_rejected_by_preview_and_create_without_writes(self):
        plan = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, tag_ids=()),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(plan, AssetTypePreviewDTO)
        before_count = AssetType.all_objects.count()
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, tag_ids=(999999,)),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in preview.issues],
            [("REFERENCE_CONFLICT", ("tag_ids",))],
        )
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, tag_ids=(999999,)),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=plan.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.assertEqual(AssetType.all_objects.count(), before_count)
        self.assertEqual(AssetTypeFieldset.objects.count(), 0)

    def test_staged_image_id_has_no_staging_authority_and_is_rejected(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=self.category.pk, staged_image_id="staged-1"),
            fieldsets=self._omitted(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in preview.issues],
            [("REFERENCE_CONFLICT", ("staged_image_id",))],
        )

    def test_required_fields_are_validated_on_create_and_preview(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.required),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        self.assertIn("REQUIRED_FIELD", [issue.code for issue in preview.issues])

        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.required),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=preview.preview_token,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIn("REQUIRED_FIELD", [issue.code for issue in result.issues])
        self.assertEqual(AssetType.all_objects.count(), 1)