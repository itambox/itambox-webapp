"""PostgreSQL-backed regressions for the Type-create preview and write commands."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from assets.models.asset import Asset
from assets.models.catalog import (
    AssetRole,
    AssetType,
    AssetTypeFieldset,
    AssetTypeImageStage,
    Category,
    CategoryDefaultFieldset,
    Depreciation,
    Manufacturer,
)
from assets.services.specifications._create_commands import _create_input_digest
from assets.services.specifications._image_staging import (
    discard_stage,
    ingest_staged_image,
)
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

# 1x1 red PNG (valid real image bytes for the Pillow/libmagic fallback).
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


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
        role_id=None,
        depreciation_id=None,
        eol_months=48,
        part_number="PN-1",
        ean="4000000000001",
        region="EU",
        configuration="64GB",
    ):
        return AssetTypeNativeCreateInputDTO(
            manufacturer_id=manufacturer_id if manufacturer_id is not None else self.manufacturer.pk,
            model=model or "Create model",
            slug=slug,
            part_number=part_number,
            ean=ean,
            region=region,
            configuration=configuration,
            eol_months=eol_months,
            category_id=category_id,
            suggested_asset_role_id=self.role.pk if role_id is None else role_id,
            depreciation_id=self.depreciation.pk if depreciation_id is None else depreciation_id,
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

    @contextmanager
    def _stage_media(self):
        """Disposable real storage for staged-image regressions."""
        storage = FileSystemStorage(location=tempfile.mkdtemp(prefix="itambox-stage-media-"))
        with patch("assets.services.specifications._image_staging.default_storage", storage):
            yield storage
        shutil.rmtree(storage.location, ignore_errors=True)

    def _new_stage(self, *, actor=None, content=None, name="type-image.png", now=None):
        return ingest_staged_image(
            actor=actor or self._actor(),
            command_kind="create_asset_type",
            content=content or _TINY_PNG,
            original_name=name,
            now=now,
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
            self._native(tag_ids=(self.tag.pk, self.tag.pk))
        with self.assertRaises(ValueError):
            self._native(staged_image_id="")
        with self.assertRaises(ValueError):
            self._native(staged_image_id="staged-1")
        with self.assertRaises(ValueError):
            self._native(staged_image_id="A" * 32)
        with self.assertRaises(ValueError):
            self._native(staged_image_id="a" * 31)
        with self.assertRaises(ValueError):
            self._native(staged_image_id="a" * 64)
        with self.assertRaises(ValueError):
            self._native(eol_months=-1)
        bounded = self._native(staged_image_id="a" * 32)
        self.assertEqual(bounded.staged_image_id, "a" * 32)
        zero_eol = self._native(eol_months=0)
        self.assertEqual(zero_eol.eol_months, 0)

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
        plan = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, slug=None),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(plan, AssetTypePreviewDTO)
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, slug="create-maker-existing-model"),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in preview.issues],
            [("REFERENCE_CONFLICT", ("slug",))],
        )
        before_count = AssetType.all_objects.count()
        before_changes = self._changes(AssetType, self.existing.pk).count()
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, slug="create-maker-existing-model"),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=plan.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("REFERENCE_CONFLICT", ("slug",))],
        )
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

    def test_native_model_limits_are_enforced_in_preview_and_write(self):
        plan = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(plan, AssetTypePreviewDTO)
        cases = (
            ({"model": "m" * 256}, ("model",)),
            ({"part_number": "p" * 101}, ("part_number",)),
            ({"ean": "1" * 15}, ("ean",)),
            ({"region": "r" * 65}, ("region",)),
            ({"configuration": "c" * 256}, ("configuration",)),
        )
        for overrides, path in cases:
            with self.subTest(path=path[0]):
                native = self._native(category_id=None, **overrides)
                preview = preview_asset_type_create(
                    actor=self._actor(),
                    native=native,
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                )
                self.assertIsInstance(preview, CommandRejectedDTO)
                self.assertEqual(
                    [(issue.code, issue.path) for issue in preview.issues],
                    [("INVALID_RANGE", path)],
                )
                result = create_asset_type(
                    actor=self._actor(),
                    native=native,
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=None,
                    expected_definition_revision=plan.expected_definition_revision,
                    expected_category_default_snapshot_revision=None,
                )
                self.assertIsInstance(result, CommandRejectedDTO)
                self.assertEqual(
                    [(issue.code, issue.path) for issue in result.issues],
                    [("INVALID_RANGE", path)],
                )
        self.assertEqual(AssetType.all_objects.count(), 1)

    def test_invalid_explicit_slug_syntax_is_rejected_in_preview_and_write(self):
        plan = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(plan, AssetTypePreviewDTO)
        native = self._native(category_id=None, slug="bad slug /")
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=native,
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in preview.issues],
            [("INVALID_TYPE", ("slug",))],
        )
        result = create_asset_type(
            actor=self._actor(),
            native=native,
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=plan.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("INVALID_TYPE", ("slug",))],
        )

    def test_zero_eol_months_matches_the_positive_integer_field_semantics(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None, eol_months=0),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None, eol_months=0),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, OwnerCreatedDTO)
        created = AssetType.all_objects.get(pk=result.owner.owner_id)
        self.assertEqual(created.eol_months, 0)

    def test_unknown_and_soft_deleted_role_depreciation_tags_are_rejected(self):
        plan = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(plan, AssetTypePreviewDTO)
        cases = (
            ({"role_id": 999999}, ("suggested_asset_role_id",)),
            ({"depreciation_id": 999999}, ("depreciation_id",)),
            ({"tag_ids": (999999,)}, ("tag_ids",)),
        )
        for overrides, path in cases:
            with self.subTest(overrides=overrides):
                preview = preview_asset_type_create(
                    actor=self._actor(),
                    native=self._native(category_id=None, **overrides),
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                )
                self.assertIsInstance(preview, CommandRejectedDTO)
                self.assertEqual(
                    [(issue.code, issue.path) for issue in preview.issues],
                    [("REFERENCE_CONFLICT", path)],
                )
                result = create_asset_type(
                    actor=self._actor(),
                    native=self._native(category_id=None, **overrides),
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=None,
                    expected_definition_revision=plan.expected_definition_revision,
                    expected_category_default_snapshot_revision=None,
                )
                self.assertIsInstance(result, CommandRejectedDTO)
                self.assertEqual(
                    [(issue.code, issue.path) for issue in result.issues],
                    [("REFERENCE_CONFLICT", path)],
                )

        self.role.delete()
        self.depreciation.delete()
        deleted_tag = Tag.objects.create(name="Doomed tag", slug="doomed-tag")
        deleted_tag.delete()
        active_role = AssetRole.objects.create(name="Active role", slug="active-role")
        active_depreciation = Depreciation.objects.create(name="Active depreciation", months=12)
        for overrides, path in (
            (
                {"role_id": self.role.pk, "depreciation_id": active_depreciation.pk},
                ("suggested_asset_role_id",),
            ),
            (
                {"role_id": active_role.pk, "depreciation_id": self.depreciation.pk},
                ("depreciation_id",),
            ),
            (
                {"role_id": active_role.pk, "depreciation_id": active_depreciation.pk, "tag_ids": (deleted_tag.pk,)},
                ("tag_ids",),
            ),
        ):
            with self.subTest(overrides=overrides):
                preview = preview_asset_type_create(
                    actor=self._actor(),
                    native=self._native(category_id=None, **overrides),
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                )
                self.assertIsInstance(preview, CommandRejectedDTO)
                self.assertEqual(
                    [(issue.code, issue.path) for issue in preview.issues],
                    [("REFERENCE_CONFLICT", path)],
                )
        self.assertEqual(AssetType.all_objects.count(), 1)

    def test_unauthorized_actor_with_broken_graph_is_object_unavailable(self):
        outsider = User.objects.create_user(username="create-outsider")
        preview = preview_asset_type_create(
            actor=ActorContextDTO(
                actor_id=outsider.pk,
                authentication_revision=authentication_revision_for_actor(outsider),
            ),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.deprecated),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in preview.issues], ["OBJECT_UNAVAILABLE"])

        result = create_asset_type(
            actor=ActorContextDTO(
                actor_id=outsider.pk,
                authentication_revision=authentication_revision_for_actor(outsider),
            ),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.deprecated),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token="any",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual([issue.code for issue in result.issues], ["OBJECT_UNAVAILABLE"])

    def test_invalid_token_with_broken_graph_is_stale_plan_not_structure(self):
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.deprecated),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token="malformed-token",
            expected_definition_revision="sha256:any",
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_PLAN"])

    def test_stale_definition_beats_deprecated_member_graph_rejection(self):
        preview = preview_asset_type_create(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.first),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, AssetTypePreviewDTO)
        CustomField.objects.filter(pk=self.first_field.pk).update(lifecycle=CustomField.LIFECYCLE_DEPRECATED)
        result = create_asset_type(
            actor=self._actor(),
            native=self._native(category_id=None),
            fieldsets=self._selection(self.first),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            preview_token=None,
            expected_definition_revision=preview.expected_definition_revision,
            expected_category_default_snapshot_revision=None,
        )
        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual(
            [(issue.code, issue.path) for issue in result.issues],
            [("STALE_DEFINITION", ("expected_definition_revision",))],
        )
        self.assertEqual(AssetType.all_objects.count(), 1)

    def test_stage_precedence_never_below_authority_token_or_revision_gates(self):
        outsider = User.objects.create_user(username="stage-outsider")
        preview = preview_asset_type_create(
            actor=ActorContextDTO(
                actor_id=outsider.pk,
                authentication_revision=authentication_revision_for_actor(outsider),
            ),
            native=self._native(category_id=None, staged_image_id="a" * 32),
            fieldsets=self._explicit_empty(),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in preview.issues], ["OBJECT_UNAVAILABLE"])

        with self._stage_media():
            stage_id = self._new_stage()
            valid = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(valid, AssetTypePreviewDTO)
            CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.second, position=2)
            stale_snapshot = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=valid.preview_token,
                expected_definition_revision=valid.expected_definition_revision,
                expected_category_default_snapshot_revision=valid.expected_category_default_snapshot_revision,
            )
            self.assertIsInstance(stale_snapshot, CommandRejectedDTO)
            self.assertIsNone(stale_snapshot.safe_owner)
            self.assertEqual(
                [issue.code for issue in stale_snapshot.issues],
                ["STALE_RESOURCE"],
            )
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")

            stale_definition_preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(stale_definition_preview, AssetTypePreviewDTO)
            CustomField.objects.filter(pk=self.first_field.pk).update(lifecycle=CustomField.LIFECYCLE_DEPRECATED)
            AssetTypeImageStage.objects.filter(stage_id=stage_id).update(
                expires_at=timezone.now() - timedelta(minutes=1)
            )
            stale_definition = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=stale_definition_preview.preview_token,
                expected_definition_revision=stale_definition_preview.expected_definition_revision,
                expected_category_default_snapshot_revision=(
                    stale_definition_preview.expected_category_default_snapshot_revision
                ),
            )
            self.assertIsInstance(stale_definition, CommandRejectedDTO)
            self.assertIsNone(stale_definition.safe_owner)
            self.assertEqual(
                [issue.code for issue in stale_definition.issues],
                ["STALE_DEFINITION"],
            )
            self.assertEqual(AssetType.all_objects.count(), 1)

    def test_staged_image_is_consumed_atomically_with_create_and_reads_back(self):
        with self._stage_media() as storage:
            stage_id = self._new_stage()
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            result = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=None,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=None,
            )
            self.assertIsInstance(result, OwnerCreatedDTO)
            created = AssetType.all_objects.get(pk=result.owner.owner_id)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(created.image.name, stage.storage_key)
            self.assertTrue(created.image.name.startswith("asset_types/"))
            self.assertEqual(stage.state, "consumed")
            self.assertEqual(stage.consumed_asset_type_id, created.pk)
            self.assertTrue(storage.exists(stage.storage_key))
            self.assertEqual(storage.open(stage.storage_key).read(), _TINY_PNG)

    def test_repeated_preview_does_not_churn_the_stage(self):
        with self._stage_media():
            stage_id = self._new_stage()
            native = self._native(category_id=None, staged_image_id=stage_id)
            first = preview_asset_type_create(
                actor=self._actor(),
                native=native,
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            second = preview_asset_type_create(
                actor=self._actor(),
                native=native,
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(first, AssetTypePreviewDTO)
            self.assertIsInstance(second, AssetTypePreviewDTO)
            self.assertEqual(first.expected_definition_revision, second.expected_definition_revision)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")
            self.assertEqual(stage.content_digest, hashlib.sha256(_TINY_PNG).hexdigest())

    def test_staged_image_replay_is_reference_conflict(self):
        with self._stage_media():
            stage_id = self._new_stage()
            native = self._native(category_id=self.category.pk, staged_image_id=stage_id)
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=native,
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            result = create_asset_type(
                actor=self._actor(),
                native=native,
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=preview.preview_token,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            )
            self.assertIsInstance(result, OwnerCreatedDTO)
            before_count = AssetType.all_objects.count()

            replay_preview = preview_asset_type_create(
                actor=self._actor(),
                native=native,
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(replay_preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in replay_preview.issues],
                [("REFERENCE_CONFLICT", ("staged_image_id",))],
            )
            replay = create_asset_type(
                actor=self._actor(),
                native=native,
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=preview.preview_token,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            )
            self.assertIsInstance(replay, CommandRejectedDTO)
            self.assertEqual([issue.code for issue in replay.issues], ["REFERENCE_CONFLICT"])
            self.assertEqual(AssetType.all_objects.count(), before_count)

    def test_staged_image_wrong_actor_and_stale_auth_are_rejected(self):
        with self._stage_media():
            stage_id = self._new_stage()
            other_user = User.objects.create_user(username="stage-other")
            other_actor = ActorContextDTO(
                actor_id=other_user.pk,
                authentication_revision=authentication_revision_for_actor(other_user),
            )
            preview = preview_asset_type_create(
                actor=other_actor,
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in preview.issues],
                [("OBJECT_UNAVAILABLE", ())],
            )

            other_user.user_permissions.add(
                Permission.objects.get(
                    content_type=ContentType.objects.get_for_model(AssetType),
                    codename="add_assettype",
                )
            )
            other_actor = ActorContextDTO(
                actor_id=other_user.pk,
                authentication_revision=authentication_revision_for_actor(other_user),
            )
            plan = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(plan, AssetTypePreviewDTO)
            preview = preview_asset_type_create(
                actor=other_actor,
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in preview.issues],
                [("REFERENCE_CONFLICT", ("staged_image_id",))],
            )
            result = create_asset_type(
                actor=other_actor,
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=None,
                expected_definition_revision=plan.expected_definition_revision,
                expected_category_default_snapshot_revision=None,
            )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in result.issues],
                [("REFERENCE_CONFLICT", ("staged_image_id",))],
            )
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")

            stale_revision = authentication_revision_for_actor(self.user)
            self.user.set_password("rotated-password")
            self.user.save(update_fields=["password"])
            stale_actor = ActorContextDTO(actor_id=self.user.pk, authentication_revision=stale_revision)
            preview = preview_asset_type_create(
                actor=stale_actor,
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in preview.issues],
                [("OBJECT_UNAVAILABLE", ())],
            )
            result = create_asset_type(
                actor=stale_actor,
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=None,
                expected_definition_revision=plan.expected_definition_revision,
                expected_category_default_snapshot_revision=None,
            )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in result.issues],
                [("OBJECT_UNAVAILABLE", ())],
            )

    def test_expired_and_discarded_stages_are_rejected(self):
        with self._stage_media():
            stage_id = self._new_stage()
            AssetTypeImageStage.objects.filter(stage_id=stage_id).update(
                expires_at=timezone.now() - timedelta(minutes=1)
            )
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in preview.issues],
                [("REFERENCE_CONFLICT", ("staged_image_id",))],
            )

            second_id = self._new_stage()
            self.assertTrue(discard_stage(second_id, self._actor(), "create_asset_type"))
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=second_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, CommandRejectedDTO)
            self.assertEqual(
                [(issue.code, issue.path) for issue in preview.issues],
                [("REFERENCE_CONFLICT", ("staged_image_id",))],
            )
            stage = AssetTypeImageStage.objects.get(stage_id=second_id)
            self.assertEqual(stage.state, "discarded")

    def test_staged_image_swapped_input_is_stale_plan(self):
        with self._stage_media():
            stage_id = self._new_stage()
            with_stage = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(with_stage, AssetTypePreviewDTO)
            swapped = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=with_stage.preview_token,
                expected_definition_revision=with_stage.expected_definition_revision,
                expected_category_default_snapshot_revision=with_stage.expected_category_default_snapshot_revision,
            )
            self.assertIsInstance(swapped, CommandRejectedDTO)
            self.assertEqual([issue.code for issue in swapped.issues], ["STALE_PLAN"])

            without_stage = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(without_stage, AssetTypePreviewDTO)
            swapped_back = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=without_stage.preview_token,
                expected_definition_revision=without_stage.expected_definition_revision,
                expected_category_default_snapshot_revision=without_stage.expected_category_default_snapshot_revision,
            )
            self.assertIsInstance(swapped_back, CommandRejectedDTO)
            self.assertEqual([issue.code for issue in swapped_back.issues], ["STALE_PLAN"])

    def test_injected_membership_failure_after_owner_save_rolls_back_stage_and_blob(self):
        with self._stage_media() as storage:
            stage_id = self._new_stage()
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id, tag_ids=(self.tag.pk,)),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            with patch(
                "assets.services.specifications._create_commands._link_memberships",
                side_effect=IntegrityError("forced membership failure"),
            ):
                result = create_asset_type(
                    actor=self._actor(),
                    native=self._native(category_id=self.category.pk, staged_image_id=stage_id, tag_ids=(self.tag.pk,)),
                    fieldsets=self._omitted(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=preview.preview_token,
                    expected_definition_revision=preview.expected_definition_revision,
                    expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
                )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual(AssetTypeFieldset.objects.count(), 0)
            self.assertEqual(AssetType.tags.through.objects.count(), 0)
            self.assertEqual(AssetType.all_objects.count(), 1)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")
            self.assertIsNone(stage.consumed_asset_type_id)
            self.assertTrue(storage.exists(stage.storage_key))

    def test_injected_tag_link_failure_rolls_back_owner_memberships_and_stage(self):
        with self._stage_media() as storage:
            stage_id = self._new_stage()
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=self.category.pk, staged_image_id=stage_id, tag_ids=(self.tag.pk,)),
                fieldsets=self._omitted(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            with patch(
                "assets.services.specifications._create_commands._link_tags",
                side_effect=IntegrityError("forced tag failure"),
            ):
                result = create_asset_type(
                    actor=self._actor(),
                    native=self._native(category_id=self.category.pk, staged_image_id=stage_id, tag_ids=(self.tag.pk,)),
                    fieldsets=self._omitted(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=preview.preview_token,
                    expected_definition_revision=preview.expected_definition_revision,
                    expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
                )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual(AssetType.all_objects.count(), 1)
            self.assertEqual(AssetTypeFieldset.objects.count(), 0)
            self.assertEqual(AssetType.tags.through.objects.count(), 0)
            self.assertEqual(self._changes(AssetType, 999999).count(), 0)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")
            self.assertTrue(storage.exists(stage.storage_key))

    def test_injected_consume_failure_rolls_back_everything(self):
        with self._stage_media() as storage:
            stage_id = self._new_stage()
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            with patch(
                "assets.services.specifications._create_commands.consume_stage",
                side_effect=IntegrityError("forced consume failure"),
            ):
                result = create_asset_type(
                    actor=self._actor(),
                    native=self._native(category_id=None, staged_image_id=stage_id),
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=None,
                    expected_definition_revision=preview.expected_definition_revision,
                    expected_category_default_snapshot_revision=None,
                )
            self.assertIsInstance(result, CommandRejectedDTO)
            self.assertEqual(AssetType.all_objects.count(), 1)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")
            self.assertIsNone(stage.consumed_asset_type_id)
            self.assertTrue(storage.exists(stage.storage_key))

    def test_enclosing_transaction_rollback_leaves_stage_pending_and_reusable(self):
        with self._stage_media() as storage:
            stage_id = self._new_stage()
            preview = preview_asset_type_create(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
            )
            self.assertIsInstance(preview, AssetTypePreviewDTO)
            with transaction.atomic():
                result = create_asset_type(
                    actor=self._actor(),
                    native=self._native(category_id=None, staged_image_id=stage_id),
                    fieldsets=self._explicit_empty(),
                    patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                    preview_token=None,
                    expected_definition_revision=preview.expected_definition_revision,
                    expected_category_default_snapshot_revision=None,
                )
                self.assertIsInstance(result, OwnerCreatedDTO)
                transaction.set_rollback(True)
            self.assertEqual(AssetType.all_objects.count(), 1)
            stage = AssetTypeImageStage.objects.get(stage_id=stage_id)
            self.assertEqual(stage.state, "pending")
            self.assertIsNone(stage.consumed_asset_type_id)
            self.assertTrue(storage.exists(stage.storage_key))

            retry = create_asset_type(
                actor=self._actor(),
                native=self._native(category_id=None, staged_image_id=stage_id),
                fieldsets=self._explicit_empty(),
                patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
                preview_token=None,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=None,
            )
            self.assertIsInstance(retry, OwnerCreatedDTO)
            created = AssetType.all_objects.get(pk=retry.owner.owner_id)
            self.assertEqual(created.image.name, stage.storage_key)

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
