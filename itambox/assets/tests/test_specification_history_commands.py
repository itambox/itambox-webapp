"""PostgreSQL-backed regressions for previewed specification-history cleanup."""

from __future__ import annotations

from datetime import timedelta
from unittest import TestCase

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase as DjangoTestCase
from django.utils import timezone

from assets.models.asset import Asset
from assets.models.catalog import AssetType, AssetTypeFieldset, Manufacturer
from assets.services.specifications._command_support import load_effective_definition, resource_revision_for_owner
from assets.services.specifications._history_support import history_state_digest, normalize_history_keys
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
    OwnerNoOpDTO,
)
from core.models import ObjectChange
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
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


class HistorySupportUnitTests(TestCase):
    def test_keys_are_sorted_and_digest_preserves_json_scalar_types(self):
        keys = normalize_history_keys(("zeta", "alpha"))
        self.assertEqual(keys, ("alpha", "zeta"))
        self.assertNotEqual(
            history_state_digest(keys, {"alpha": 1, "zeta": True}),
            history_state_digest(keys, {"alpha": True, "zeta": 1}),
        )

    def test_duplicate_history_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_history_keys(("legacy", "legacy"))


class SpecificationHistoryCommandTests(DjangoTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="History tenant", slug="history-tenant")
        self.other_tenant = Tenant.objects.create(name="Other history tenant", slug="other-history-tenant")
        self.user = User.objects.create_user(username="history-editor")
        membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        role = Role.objects.create(
            tenant=self.tenant,
            name="History editor",
            permissions=["assets.change_asset"],
        )
        self.grant = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason="T10 history test authorization",
            valid_until=timezone.now() + timedelta(days=1),
        )
        RoleGrantScope.objects.create(
            role_grant=self.grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.manufacturer = Manufacturer.objects.create(name="History maker", slug="history-maker")
        self.current_field = CustomField.objects.create(
            name="active_history",
            namespace="local",
            label="Active history",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.current_field.object_types.add(ContentType.objects.get_for_model(AssetType))
        self.fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="history-fields",
            label="History fields",
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(fieldset=self.fieldset, custom_field=self.current_field, position=1)
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="History type",
            slug="history-type",
        )
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.fieldset, position=1)
        AssetType.all_objects.filter(pk=self.asset_type.pk).update(
            custom_field_data={"ghost_history": "legacy", "active_history": "current"},
        )
        self.asset_type.refresh_from_db()
        self.asset = Asset.objects.create(
            name="History asset",
            asset_tag="HISTORY-1",
            tenant=self.tenant,
            asset_type=self.asset_type,
        )
        Asset._base_manager.filter(pk=self.asset.pk).update(custom_field_data={"asset_ghost": "legacy"})
        self.other_asset = Asset.objects.create(
            name="Other history asset",
            asset_tag="HISTORY-2",
            tenant=self.other_tenant,
            asset_type=self.asset_type,
        )
        Asset._base_manager.filter(pk=self.other_asset.pk).update(custom_field_data={"asset_ghost": "secret"})
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(AssetType),
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

    def test_type_unknown_key_cleanup_removes_value_and_audits_raw_history(self):
        resource_revision, definition_revision = self._type_revisions()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("ghost_history",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        self.assertEqual(preview.keys, ("ghost_history",))
        self.assertEqual(preview.issues, ())
        self.assertEqual(
            preview.historical_state_digest,
            history_state_digest(
                ("ghost_history",),
                {
                    "ghost_history": "legacy",
                },
            ),
        )
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
        self.assertEqual(self.asset_type.custom_field_data, {"active_history": "current"})
        change = self._changes(AssetType, self.asset_type.pk).order_by("-pk").first()
        self.assertIsNotNone(change)
        self.assertEqual(change.user_id, self.user.pk)
        self.assertEqual(change.prechange_data["custom_field_data"]["ghost_history"], "legacy")
        self.assertNotIn("ghost_history", change.postchange_data["custom_field_data"])

    def test_active_key_is_rejected_without_a_write(self):
        resource_revision, definition_revision = self._type_revisions()
        before_changes = self._changes(AssetType, self.asset_type.pk).count()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("active_history",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        self.assertEqual([issue.code for issue in preview.issues], ["READ_ONLY_FIELD"])
        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["READ_ONLY_FIELD"])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data["active_history"], "current")
        self.assertEqual(self._changes(AssetType, self.asset_type.pk).count(), before_changes)

    def test_mixed_valid_and_active_selection_rolls_back_everything(self):
        resource_revision, definition_revision = self._type_revisions()
        before = dict(self.asset_type.custom_field_data)
        before_changes = self._changes(AssetType, self.asset_type.pk).count()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("active_history", "ghost_history"),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
        self.assertEqual([issue.code for issue in preview.issues], ["READ_ONLY_FIELD"])
        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, before)
        self.assertEqual(self._changes(AssetType, self.asset_type.pk).count(), before_changes)

    def test_stale_owner_revision_is_rejected_before_cleanup(self):
        resource_revision, definition_revision = self._type_revisions()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("ghost_history",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )
        AssetType.all_objects.filter(pk=self.asset_type.pk).update(model="History type changed")

        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_RESOURCE"])
        self.asset_type.refresh_from_db()
        self.assertIn("ghost_history", self.asset_type.custom_field_data)

    def test_stale_definition_revision_is_rejected_before_cleanup(self):
        resource_revision, definition_revision = self._type_revisions()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("ghost_history",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )
        CustomField.objects.filter(pk=self.current_field.pk).update(label="Definition changed")

        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_DEFINITION"])
        self.asset_type.refresh_from_db()
        self.assertIn("ghost_history", self.asset_type.custom_field_data)

    def test_token_key_mismatch_is_rejected_without_a_write(self):
        resource_revision, definition_revision = self._type_revisions()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("ghost_history",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=("active_history",),
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertEqual([issue.code for issue in result.issues], ["STALE_PLAN"])
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data["ghost_history"], "legacy")

    def test_asset_cross_tenant_access_is_nondisclosing(self):
        resource_revision, definition_revision = self._asset_revisions(self.other_asset)
        authorization = self._asset_authorization()

        preview = preview_asset_history_cleanup(
            authorization=authorization,
            asset_id=self.other_asset.pk,
            keys=("asset_ghost",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )

        self.assertIsInstance(preview, CommandRejectedDTO)
        self.assertIsNone(preview.safe_owner)
        self.assertEqual([issue.code for issue in preview.issues], ["OBJECT_UNAVAILABLE"])
        self.other_asset.refresh_from_db()
        self.assertEqual(self.other_asset.custom_field_data, {"asset_ghost": "secret"})

    def test_asset_revocation_is_rejected_before_token_and_write(self):
        resource_revision, definition_revision = self._asset_revisions()
        authorization = self._asset_authorization()
        preview = preview_asset_history_cleanup(
            authorization=authorization,
            asset_id=self.asset.pk,
            keys=("asset_ghost",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )
        self.grant.delete()

        result = cleanup_asset_history(
            authorization=authorization,
            asset_id=self.asset.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, CommandRejectedDTO)
        self.assertIsNone(result.safe_owner)
        self.assertEqual([issue.code for issue in result.issues], ["OBJECT_UNAVAILABLE"])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.custom_field_data, {"asset_ghost": "legacy"})

    def test_asset_unknown_key_cleanup_is_audited(self):
        resource_revision, definition_revision = self._asset_revisions()
        authorization = self._asset_authorization()
        preview = preview_asset_history_cleanup(
            authorization=authorization,
            asset_id=self.asset.pk,
            keys=("asset_ghost",),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )
        self.assertIsInstance(preview, HistoryCleanupPreviewDTO)
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
        self.assertEqual(self.asset.custom_field_data, {})
        change = self._changes(Asset, self.asset.pk).order_by("-pk").first()
        self.assertIsNotNone(change)
        self.assertEqual(change.prechange_data["custom_field_data"]["asset_ghost"], "legacy")

    def test_empty_selection_is_a_no_op(self):
        resource_revision, definition_revision = self._type_revisions()
        before_timestamp = self.asset_type.updated_at
        before_changes = self._changes(AssetType, self.asset_type.pk).count()
        preview = preview_asset_type_history_cleanup(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=(),
            expected_resource_revision=resource_revision,
            expected_definition_revision=definition_revision,
        )
        result = cleanup_asset_type_history(
            actor=self._actor(),
            asset_type_id=self.asset_type.pk,
            keys=preview.keys,
            preview_token=preview.preview_token,
            expected_resource_revision=preview.expected_resource_revision,
            expected_definition_revision=preview.expected_definition_revision,
        )

        self.assertIsInstance(result, OwnerNoOpDTO)
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.updated_at, before_timestamp)
        self.assertEqual(self._changes(AssetType, self.asset_type.pk).count(), before_changes)
