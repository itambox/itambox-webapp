"""Final boundary regressions for previewed specification-history cleanup."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase as DjangoTestCase
from django.utils import timezone

from assets.models.asset import Asset
from assets.models.catalog import AssetType, AssetTypeFieldset, Manufacturer
from assets.services.specifications._command_support import load_effective_definition, resource_revision_for_owner
from assets.services.specifications.commands import (
    cleanup_asset_history,
    cleanup_asset_type_history,
    preview_asset_history_cleanup,
    preview_asset_type_history_cleanup,
)
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    HistoryCleanupPreviewDTO,
    OwnerChangedDTO,
)
from core.models import ObjectChange
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
)
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.access_scope import (
    AccessScopeResolutionRequestDTO,
    AccessScopeResolvedDTO,
    ActorContextDTO,
    RequestedScopeSelectorDTO,
    ResolvedAccessAuthorizationDTO,
    authentication_revision_for_actor,
    resolve_access_scope,
)

User = get_user_model()
_HISTORY_KEYS = ("inactive_history", "deprecated_history", "deprecated_choice_history")


class HistoryBoundaryFixtureMixin:
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="History boundary tenant", slug="history-boundary-tenant")
        self.other_tenant = Tenant.objects.create(
            name="Other history boundary tenant", slug="other-history-boundary-tenant"
        )
        self.user = User.objects.create_user(username="history-boundary-editor")
        membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        role = Role.objects.create(
            tenant=self.tenant,
            name="History boundary editor",
            permissions=["assets.change_asset"],
        )
        self.grant = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason="T10 final history boundary authorization",
            valid_until=timezone.now() + timedelta(days=1),
        )
        RoleGrantScope.objects.create(role_grant=self.grant, scope_type=RoleGrantScope.SCOPE_OWN)

        self.manufacturer = Manufacturer.objects.create(
            name="History boundary maker",
            slug="history-boundary-maker",
        )
        asset_type_ct = ContentType.objects.get_for_model(AssetType)
        asset_ct = ContentType.objects.get_for_model(Asset)

        self.active_field = CustomField.objects.create(
            name="active_history",
            namespace="local",
            label="Active history",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.inactive_field = CustomField.objects.create(
            name="inactive_history",
            namespace="local",
            label="Inactive history",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.deprecated_field = CustomField.objects.create(
            name="deprecated_history",
            namespace="local",
            label="Deprecated history",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="history-boundary-choice",
            label="History boundary choices",
            management_kind=CustomFieldChoiceSet.MANAGEMENT_LOCAL,
        )
        self.old_choice = CustomFieldChoice.objects.create(
            choice_set=self.choice_set,
            key="old_choice",
            label="Old choice",
            position=1,
            lifecycle=CustomFieldChoice.LIFECYCLE_DEPRECATED,
        )
        self.deprecated_choice_field = CustomField.objects.create(
            name="deprecated_choice_history",
            namespace="local",
            label="Deprecated choice history",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_COMPOSED,
            choice_set=self.choice_set,
            max_values=1,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        for field in (
            self.active_field,
            self.inactive_field,
            self.deprecated_field,
            self.deprecated_choice_field,
        ):
            field.object_types.add(asset_type_ct, asset_ct)

        self.fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="history-boundary-fields",
            label="History boundary fields",
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(fieldset=self.fieldset, custom_field=self.active_field, position=1)
        CustomFieldsetField.objects.create(fieldset=self.fieldset, custom_field=self.deprecated_field, position=2)
        CustomFieldsetField.objects.create(
            fieldset=self.fieldset,
            custom_field=self.deprecated_choice_field,
            position=3,
        )
        self.inactive_fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="history-boundary-inactive",
            label="History boundary inactive",
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(
            fieldset=self.inactive_fieldset,
            custom_field=self.inactive_field,
            position=1,
        )

        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="History boundary type",
            slug="history-boundary-type",
        )
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.fieldset, position=1)
        AssetType.all_objects.filter(pk=self.asset_type.pk).update(
            custom_field_data={
                "active_history": "type-active",
                "inactive_history": "type-inactive",
                "deprecated_history": "type-deprecated",
                "deprecated_choice_history": "old_choice",
            }
        )
        self.asset_type.refresh_from_db()

        self.asset = Asset.objects.create(
            name="History boundary asset",
            asset_tag="HISTORY-BOUNDARY-1",
            tenant=self.tenant,
            asset_type=self.asset_type,
        )
        Asset._base_manager.filter(pk=self.asset.pk).update(
            custom_field_data={
                "active_history": "asset-active",
                "inactive_history": "asset-inactive",
                "deprecated_history": "asset-deprecated",
                "deprecated_choice_history": "old_choice",
            }
        )
        self.asset.refresh_from_db()
        self.other_asset = Asset.objects.create(
            name="Other history boundary asset",
            asset_tag="HISTORY-BOUNDARY-2",
            tenant=self.other_tenant,
            asset_type=self.asset_type,
        )
        Asset._base_manager.filter(pk=self.other_asset.pk).update(
            custom_field_data={"asset_ghost": "other-tenant-secret"}
        )
        self.other_asset.refresh_from_db()

        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=asset_type_ct,
                codename="change_assettype",
            )
        )

    def _actor(self):
        return ActorContextDTO(
            actor_id=self.user.pk,
            authentication_revision=authentication_revision_for_actor(self.user),
        )

    def _type_revisions(self):
        owner = AssetType.all_objects.get(pk=self.asset_type.pk)
        definition, _definitions = load_effective_definition(
            owner.pk,
            "asset_type",
            tuple(owner.custom_field_data),
        )
        return resource_revision_for_owner(owner), definition.revision

    def _asset_revisions(self, asset=None):
        owner = Asset._base_manager.get(pk=(asset or self.asset).pk)
        definition, _definitions = load_effective_definition(
            owner.asset_type_id,
            "asset",
            tuple(owner.custom_field_data),
        )
        return resource_revision_for_owner(owner), definition.revision

    def _asset_authorization(self, tenant=None):
        actor = self._actor()
        request = AccessScopeResolutionRequestDTO(
            actor=actor,
            selector=RequestedScopeSelectorDTO(
                mode="tenant",
                tenant_id=(tenant or self.tenant).pk,
                tenant_group_id=None,
            ),
            operation="cleanup_asset_specification_history",
            required_permission="assets.change_asset",
        )
        resolved = resolve_access_scope(request)
        self.assertIsInstance(resolved, AccessScopeResolvedDTO)
        return ResolvedAccessAuthorizationDTO(
            actor=actor,
            request=request,
            initial_scope=resolved.access_scope,
        )

    def _changes(self, model, pk):
        return ObjectChange._base_manager.filter(
            changed_object_type=ContentType.objects.get_for_model(model),
            changed_object_id=pk,
        )

    def _preview_type(self, keys=_HISTORY_KEYS):
        resource_revision, definition_revision = self._type_revisions()
        return preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=keys,
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

    def _preview_asset(self, keys=_HISTORY_KEYS, asset=None, authorization=None):
        owner = asset or self.asset
        resource_revision, definition_revision = self._asset_revisions(owner)
        return preview_asset_history_cleanup(
            authorization=authorization or self._asset_authorization(),
            asset_id=owner.pk,
            keys=keys,
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )


class SpecificationHistoryBoundaryTests(HistoryBoundaryFixtureMixin, DjangoTestCase):
    def test_type_cleanup_accepts_all_historical_projection_reasons_and_preserves_active_sibling(self):
        preview = self._preview_type()
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        self.assertEqual(preview.issues, ())
        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, OwnerChangedDTO)
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, {"active_history": "type-active"})
        change = self._changes(AssetType, self.asset_type.pk).order_by("-pk").first()
        self.assertIsNotNone(change)
        self.assertEqual(
            change.prechange_data["custom_field_data"],
            {
                "active_history": "type-active",
                "inactive_history": "type-inactive",
                "deprecated_history": "type-deprecated",
                "deprecated_choice_history": "old_choice",
            },
        )
        self.assertEqual(change.postchange_data["custom_field_data"], {"active_history": "type-active"})

    def test_asset_cleanup_accepts_all_historical_projection_reasons_and_preserves_active_sibling(self):
        preview = self._preview_asset()
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        self.assertEqual(preview.issues, ())
        authorization = self._asset_authorization()
        result = cleanup_asset_history(
            authorization=authorization,
            asset_id=self.asset.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, OwnerChangedDTO)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.custom_field_data, {"active_history": "asset-active"})
        change = self._changes(Asset, self.asset.pk).order_by("-pk").first()
        self.assertIsNotNone(change)
        self.assertEqual(
            change.prechange_data["custom_field_data"],
            {
                "active_history": "asset-active",
                "inactive_history": "asset-inactive",
                "deprecated_history": "asset-deprecated",
                "deprecated_choice_history": "old_choice",
            },
        )
        self.assertEqual(change.postchange_data["custom_field_data"], {"active_history": "asset-active"})

    def test_type_token_rejects_raw_int_to_bool_history_mutation_without_any_write(self):
        AssetType.all_objects.filter(pk=self.asset_type.pk).update(
            custom_field_data={
                **self.asset_type.custom_field_data,
                "inactive_history": 1,
            }
        )
        self.asset_type.refresh_from_db()
        preview = self._preview_type(keys=("inactive_history",))
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        before_updated_at = self.asset_type.updated_at
        before_changes = self._changes(AssetType, self.asset_type.pk).count()
        AssetType.all_objects.filter(pk=self.asset_type.pk).update(
            custom_field_data={
                **self.asset_type.custom_field_data,
                "inactive_history": True,
            }
        )

        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_PLAN"])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data["inactive_history"], True)
        self.assertEqual(self.asset_type.updated_at, before_updated_at)
        self.assertEqual(self._changes(AssetType, self.asset_type.pk).count(), before_changes)

    def test_asset_token_rejects_raw_int_to_bool_history_mutation_without_any_write(self):
        Asset._base_manager.filter(pk=self.asset.pk).update(
            custom_field_data={
                **self.asset.custom_field_data,
                "inactive_history": 1,
            }
        )
        self.asset.refresh_from_db()
        authorization = self._asset_authorization()
        preview = self._preview_asset(keys=("inactive_history",), authorization=authorization)
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        before_updated_at = self.asset.updated_at
        before_changes = self._changes(Asset, self.asset.pk).count()
        Asset._base_manager.filter(pk=self.asset.pk).update(
            custom_field_data={
                **self.asset.custom_field_data,
                "inactive_history": True,
            }
        )

        result = cleanup_asset_history(
            authorization=authorization,
            asset_id=self.asset.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_PLAN"])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.custom_field_data["inactive_history"], True)
        self.assertEqual(self.asset.updated_at, before_updated_at)
        self.assertEqual(self._changes(Asset, self.asset.pk).count(), before_changes)

    def test_type_audited_save_validation_error_rolls_back_owner_timestamp_and_audit(self):
        preview = self._preview_type(keys=("inactive_history",))
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        before_values = dict(self.asset_type.custom_field_data)
        before_updated_at = self.asset_type.updated_at
        before_changes = self._changes(AssetType, self.asset_type.pk).count()
        original_save = AssetType.save

        def save_then_raise(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise ValidationError("injected post-audit save failure")

        with patch.object(AssetType, "save", save_then_raise):
            result = cleanup_asset_type_history(
                actor=self._actor(),
                asset_type_id=self.asset_type.pk,
                keys=preview.keys,
                preview_token=preview.preview_token,
                expected_resource_revision=preview.expected_resource_revision,
                expected_definition_revision=preview.expected_definition_revision,
            )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, before_values)
        self.assertEqual(self.asset_type.updated_at, before_updated_at)
        self.assertEqual(self._changes(AssetType, self.asset_type.pk).count(), before_changes)

    def test_asset_audited_save_validation_error_rolls_back_owner_timestamp_and_audit(self):
        authorization = self._asset_authorization()
        preview = self._preview_asset(keys=("inactive_history",), authorization=authorization)
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        before_values = dict(self.asset.custom_field_data)
        before_updated_at = self.asset.updated_at
        before_changes = self._changes(Asset, self.asset.pk).count()
        original_save = Asset.save

        def save_then_raise(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise ValidationError("injected post-audit save failure")

        with patch.object(Asset, "save", save_then_raise):
            result = cleanup_asset_history(
                authorization=authorization,
                asset_id=self.asset.pk,
                keys=preview.keys,
                preview_token=preview.preview_token,
                expected_resource_revision=preview.expected_resource_revision,
                expected_definition_revision=preview.expected_definition_revision,
            )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["REFERENCE_CONFLICT"])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.custom_field_data, before_values)
        self.assertEqual(self.asset.updated_at, before_updated_at)
        self.assertEqual(self._changes(Asset, self.asset.pk).count(), before_changes)

    def test_cross_tenant_write_with_invalid_token_and_mixed_keys_is_nondisclosing(self):
        authorization = self._asset_authorization()
        resource_revision, definition_revision = self._asset_revisions(self.other_asset)
        before_values = dict(self.other_asset.custom_field_data)
        before_updated_at = self.other_asset.updated_at
        before_changes = self._changes(Asset, self.other_asset.pk).count()

        result = cleanup_asset_history(
            authorization=authorization,
            asset_id=self.other_asset.pk,
            keys=("asset_ghost", "not_a_real_history_key"),
            preview_token="not-a-valid-preview-token",
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual([issue.code for issue in result.issues], ["OBJECT_UNAVAILABLE"])
        self.other_asset.refresh_from_db()
        self.assertEqual(self.other_asset.custom_field_data, before_values)
        self.assertEqual(self.other_asset.updated_at, before_updated_at)
        self.assertEqual(self._changes(Asset, self.other_asset.pk).count(), before_changes)

    def test_wrong_asset_history_operation_and_permission_are_rejected(self):
        authorization = self._asset_authorization()
        resource_revision, definition_revision = self._asset_revisions()
        for request in (
            replace(authorization.request, operation="update_asset_specifications"),
            replace(authorization.request, required_permission="assets.view_asset"),
        ):
            with self.subTest(request=request):
                bad_authorization = replace(authorization, request=request)
                result = cleanup_asset_history(
                    authorization=bad_authorization,
                    asset_id=self.asset.pk,
                    keys=("inactive_history",),
                    preview_token="not-a-valid-preview-token",
                    expected_resource_revision=resource_revision,
                    expected_definition_revision=definition_revision,
                )
                self.assertIsInstance(result, CommandRejectedDTO)
                self.assertIsNone(result.safe_owner)
                self.assertEqual([issue.code for issue in result.issues], ["OBJECT_UNAVAILABLE"])
