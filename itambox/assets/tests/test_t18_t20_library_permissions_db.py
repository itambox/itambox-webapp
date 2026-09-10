"""Focused real-ORM permission contract tests for Type Library commands.

These tests deliberately create the pending manage capability locally because the
parent owns its model Meta/migration/audit registration. They are collection-safe
on this worker and are intended for the parent's migrated execution lane.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.models.catalog import AssetType, Category, Manufacturer
from assets.services.type_library.application import LibraryApplyError, LibraryApplyRequest
from assets.services.type_library.commands import (
    LibraryCommandError,
    apply_library,
    export_library,
    preview_library,
)
from assets.tests.test_t17_type_library_validation import _release_document
from core.models import ObjectChange
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    SpecificationLibrary,
    SpecificationLibraryRelease,
)
from organization.services.access_scope import authentication_revision_for_actor

_MANAGE_PERMISSION = "manage_specification_library"
_ACTION_MODELS = (
    AssetType,
    Category,
    Manufacturer,
    CustomField,
    CustomFieldset,
    CustomFieldChoiceSet,
    CustomFieldChoice,
)


class LibraryPermissionContractTests(TestCase):
    def setUp(self):
        self.signing_key = b"library-permission-contract-test-key"
        self.document = _release_document()

    @staticmethod
    def _raw(document):
        return json.dumps(document, ensure_ascii=False)

    def _permission(self, model, action):
        content_type = ContentType.objects.get_for_model(model)
        codename = f"{action}_{model._meta.model_name}"
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": codename},
        )
        return permission

    def _grant_manage(self, actor):
        permission, _ = Permission.objects.get_or_create(
            content_type=ContentType.objects.get_for_model(SpecificationLibrary),
            codename=_MANAGE_PERMISSION,
            defaults={"name": "Can manage specification libraries"},
        )
        actor.user_permissions.add(permission)

    def _grant_actions(self, actor):
        for model in _ACTION_MODELS:
            actor.user_permissions.add(self._permission(model, "add"))
            actor.user_permissions.add(self._permission(model, "change"))

    def _actor(self, username, *, staff=False):
        return get_user_model().objects.create_user(username=username, is_staff=staff)

    def _preview(self, actor, document=None):
        return preview_library(
            self._raw(document or self.document),
            actor=actor,
            signing_key=self.signing_key,
        )

    def _request(self, actor, preview):
        fresh = get_user_model().objects.get(pk=actor.pk)
        return LibraryApplyRequest(
            plan=preview.plan,
            token=preview.preview_token,
            actor_id=fresh.pk,
            authentication_revision=authentication_revision_for_actor(fresh),
            access_scope_fingerprint=None,
            signing_key=self.signing_key,
        )

    def test_manage_only_cannot_preview_or_apply_plan_model_actions_but_can_export(self):
        actor = self._actor("library-manage-only")
        self._grant_manage(actor)
        with self.assertRaises(LibraryCommandError) as preview_error:
            self._preview(actor)
        self.assertEqual(preview_error.exception.code, "OBJECT_UNAVAILABLE")

        seed = self._actor("library-seed-superuser")
        seed.is_superuser = True
        seed.save(update_fields=["is_superuser"])
        seed_preview = self._preview(seed)
        apply_library(
            self._raw(self.document),
            self._request(seed, seed_preview),
            actor=seed,
        )
        exported = export_library("acme", actor=actor, mode="original_release")
        self.assertEqual(exported.namespace, "acme")
        self.assertEqual(SpecificationLibraryRelease.objects.count(), 1)

    def test_non_superuser_with_manage_and_global_action_set_can_apply_and_export(self):
        actor = self._actor("library-global-actions")
        self._grant_manage(actor)
        self._grant_actions(actor)

        preview = self._preview(actor)
        result = apply_library(
            self._raw(self.document),
            self._request(actor, preview),
            actor=actor,
        )

        self.assertEqual(result.release, 1)
        self.assertTrue(SpecificationLibrary.objects.filter(namespace="acme").exists())
        self.assertEqual(export_library("acme", actor=actor, mode="original_release").namespace, "acme")

    def test_staff_or_direct_wrong_capability_is_not_a_manage_shortcut(self):
        actor = self._actor("library-staff-with-actions", staff=True)
        self._grant_actions(actor)
        actor.user_permissions.add(self._permission(SpecificationLibrary, "change"))

        with self.assertRaises(LibraryCommandError) as denied:
            self._preview(actor)
        self.assertEqual(denied.exception.code, "OBJECT_UNAVAILABLE")

    def test_revoke_between_preview_and_apply_leaves_rows_and_audit_unchanged(self):
        actor = self._actor("library-revocation")
        self._grant_manage(actor)
        self._grant_actions(actor)
        preview = self._preview(actor)
        baseline_changes = ObjectChange._base_manager.count()
        baseline_libraries = SpecificationLibrary.objects.count()
        baseline_releases = SpecificationLibraryRelease.objects.count()

        actor.user_permissions.remove(self._permission(AssetType, "add"))
        with self.assertRaises(LibraryApplyError) as denied:
            apply_library(
                self._raw(self.document),
                self._request(actor, preview),
                actor=actor,
            )
        self.assertEqual(denied.exception.code, "OBJECT_UNAVAILABLE")
        self.assertEqual(ObjectChange._base_manager.count(), baseline_changes)
        self.assertEqual(SpecificationLibrary.objects.count(), baseline_libraries)
        self.assertEqual(SpecificationLibraryRelease.objects.count(), baseline_releases)

    def test_noop_reapply_needs_manage_but_not_model_action_churn(self):
        actor = self._actor("library-noop")
        self._grant_manage(actor)
        self._grant_actions(actor)
        first_preview = self._preview(actor)
        apply_library(
            self._raw(self.document),
            self._request(actor, first_preview),
            actor=actor,
        )
        for model in _ACTION_MODELS:
            actor.user_permissions.remove(self._permission(model, "add"))
            actor.user_permissions.remove(self._permission(model, "change"))
        baseline_changes = ObjectChange._base_manager.count()
        second_preview = self._preview(actor)
        result = apply_library(
            self._raw(self.document),
            self._request(actor, second_preview),
            actor=actor,
        )
        self.assertTrue(result.no_op)
        self.assertEqual(ObjectChange._base_manager.count(), baseline_changes)
