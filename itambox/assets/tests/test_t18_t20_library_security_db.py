"""Focused DB qualification for Library authorization, audit, and conflict journeys."""

from __future__ import annotations

import json
from copy import deepcopy

from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.models.catalog import AssetType
from assets.services.type_library.application import LibraryApplyError, LibraryApplyRequest
from assets.services.type_library.commands import (
    LibraryCommandError,
    apply_library,
    export_library,
    preview_library,
)
from assets.tests.test_t17_type_library_validation import _release_document
from core.models import ObjectChange
from extras.models import SpecificationLibrary, SpecificationLibraryRelease
from organization.services.access_scope import authentication_revision_for_actor


class LibraryCommandSecurityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="library-security-test",
            password=None,
        )
        self.signing_key = b"library-security-test-key"

    @staticmethod
    def _raw(document):
        return json.dumps(document, ensure_ascii=False)

    def _preview(self, document, *, resolutions=None, actor=None):
        return preview_library(
            self._raw(document),
            actor=actor or self.actor,
            signing_key=self.signing_key,
            resolutions=resolutions,
        )

    def _request(
        self,
        preview,
        *,
        actor=None,
        actor_id=None,
        authentication_revision=None,
    ):
        revision_actor = get_user_model().objects.get(pk=self.actor.pk if actor_id is None else actor_id)
        return LibraryApplyRequest(
            plan=preview.plan,
            token=preview.preview_token,
            actor_id=self.actor.pk if actor_id is None else actor_id,
            authentication_revision=authentication_revision or authentication_revision_for_actor(revision_actor),
            access_scope_fingerprint=None,
            signing_key=self.signing_key,
        )

    def _apply(self, document, preview, *, actor=None, actor_id=None, authentication_revision=None):
        return apply_library(
            self._raw(document),
            self._request(
                preview,
                actor=actor,
                actor_id=actor_id,
                authentication_revision=authentication_revision,
            ),
            actor=actor or self.actor,
        )

    def _seed(self):
        document = _release_document()
        preview = self._preview(document)
        result = self._apply(document, preview)
        return document, preview, result, SpecificationLibrary.objects.get(namespace="acme")

    def test_fresh_actor_authorization_rejects_revocation_demotion_and_wrong_actor_without_writes(self):
        document, preview, _, library = self._seed()
        baseline_changes = ObjectChange._base_manager.count()
        baseline_releases = SpecificationLibraryRelease.objects.count()
        baseline_asset_type = AssetType.all_objects.get(library=library)
        baseline_updated_at = baseline_asset_type.updated_at

        auth_revision = authentication_revision_for_actor(self.actor)
        get_user_model()._base_manager.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(LibraryCommandError) as preview_error:
            self._preview(document)
        self.assertEqual(preview_error.exception.code, "OBJECT_UNAVAILABLE")
        with self.assertRaises(LibraryApplyError) as apply_error:
            self._apply(document, preview, authentication_revision=auth_revision)
        self.assertEqual(apply_error.exception.code, "OBJECT_UNAVAILABLE")
        with self.assertRaises(LibraryApplyError) as denied_invalid_source:
            apply_library(
                "not-json",
                self._request(
                    preview,
                    authentication_revision=auth_revision,
                ),
                actor=self.actor,
            )
        self.assertEqual(denied_invalid_source.exception.code, "OBJECT_UNAVAILABLE")
        after_inactive_failure = ObjectChange._base_manager.count()

        get_user_model()._base_manager.filter(pk=self.actor.pk).update(is_active=True, is_superuser=False, is_staff=False)
        with self.assertRaises(LibraryApplyError) as demotion_error:
            self._apply(document, preview, authentication_revision=auth_revision)
        self.assertEqual(demotion_error.exception.code, "OBJECT_UNAVAILABLE")
        self.assertEqual(ObjectChange._base_manager.count(), after_inactive_failure)

        get_user_model()._base_manager.filter(pk=self.actor.pk).update(is_superuser=True, is_staff=True)
        rotated = get_user_model().objects.get(pk=self.actor.pk)
        rotated.set_password("rotated-library-password")
        rotated.save(update_fields=["password"])
        with self.assertRaises(LibraryApplyError) as password_error:
            self._apply(document, preview, authentication_revision=auth_revision)
        self.assertEqual(password_error.exception.code, "STALE_PLAN")
        after_password_failure = ObjectChange._base_manager.count()
        other = get_user_model().objects.create_superuser(username="library-security-replay", password=None)
        with self.assertRaises(LibraryApplyError) as replay_error:
            self._apply(document, preview, actor=other, actor_id=self.actor.pk, authentication_revision=auth_revision)
        self.assertEqual(replay_error.exception.code, "OBJECT_UNAVAILABLE")
        self.assertEqual(ObjectChange._base_manager.count(), after_password_failure)

        self.assertGreaterEqual(ObjectChange._base_manager.count(), baseline_changes)
        self.assertEqual(SpecificationLibraryRelease.objects.count(), baseline_releases)
        baseline_asset_type.refresh_from_db()
        self.assertEqual(baseline_asset_type.updated_at, baseline_updated_at)

    def test_noop_reapply_is_audited_once_and_public_resolution_rechecks_current_state(self):
        document, preview, _, library = self._seed()
        asset_type = AssetType.all_objects.get(library=library)
        baseline_changes = ObjectChange._base_manager.count()
        actor_changes = ObjectChange._base_manager.filter(user_id=self.actor.pk)
        baseline_updated_at = asset_type.updated_at
        self.assertTrue(actor_changes.exists())
        self.assertTrue(
            ObjectChange._base_manager.filter(
                user_id=self.actor.pk,
                changed_object_id=library.pk,
            ).exists()
        )

        no_op = self._apply(document, self._preview(document))
        self.assertTrue(no_op.no_op)
        library.refresh_from_db()
        asset_type.refresh_from_db()
        self.assertEqual(ObjectChange._base_manager.count(), baseline_changes)
        self.assertEqual(asset_type.updated_at, baseline_updated_at)

        incoming = deepcopy(document)
        incoming["library"]["release"] = 2
        incoming["definitions"]["asset_types"][0]["description"] = "upstream description"
        AssetType.all_objects.filter(pk=asset_type.pk).update(description="local description")
        conflict_preview = self._preview(incoming)
        conflicts = [action for action in conflict_preview.plan.actions if action.action == "conflict"]
        self.assertTrue(conflicts)
        with self.assertRaises(LibraryCommandError) as invalid_resolution:
            self._preview(incoming, resolutions={"not-an-action": "take_upstream"})
        self.assertEqual(invalid_resolution.exception.code, "INVALID_RESOLUTION")

        decision = {action.action_id: "take_upstream" for action in conflicts}
        resolved_preview = self._preview(incoming, resolutions=decision)
        self.assertTrue(resolved_preview.plan.can_apply)
        AssetType.all_objects.filter(pk=asset_type.pk).update(description="second local description")
        with self.assertRaises(LibraryApplyError) as stale_error:
            self._apply(incoming, resolved_preview)
        self.assertEqual(stale_error.exception.code, "STALE_PLAN")
        self.assertEqual(SpecificationLibraryRelease.objects.filter(library=library).count(), 1)

        fresh_conflict_preview = self._preview(incoming)
        fresh_decision = {
            action.action_id: "take_upstream"
            for action in fresh_conflict_preview.plan.actions
            if action.action == "conflict"
        }
        fresh_resolved_preview = self._preview(incoming, resolutions=fresh_decision)
        self._apply(incoming, fresh_resolved_preview)
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.description, "upstream description")

    def test_fork_uses_effective_override_and_round_trip_export_is_stable(self):
        _, _, _, library = self._seed()
        asset_type = AssetType.all_objects.get(library=library)
        AssetType.all_objects.filter(pk=asset_type.pk).update(description="local customization")

        original = export_library("acme", actor=self.actor, mode="original_release")
        effective = export_library(
            "acme",
            actor=self.actor,
            mode="effective_snapshot",
            acknowledge_retained_history=True,
        )
        fork = export_library("acme", actor=self.actor, mode="fork", new_namespace="acme-fork")

        self.assertNotEqual(original.document["definitions"]["asset_types"][0]["description"], "local customization")
        self.assertEqual(effective.source_digest, original.semantic_digest)
        self.assertEqual(effective.document["upstream"]["library"]["namespace"], "acme")
        self.assertEqual(
            effective.document["effective_definitions"]["asset_types"][0]["description"],
            "local customization",
        )
        self.assertEqual(fork.namespace, "acme-fork")
        self.assertEqual(fork.document["definitions"]["asset_types"][0]["description"], "local customization")

        fork_preview = self._preview(fork.document)
        self._apply(fork.document, fork_preview)
        repeat_one = export_library(
            "acme-fork",
            actor=self.actor,
            mode="effective_snapshot",
            acknowledge_retained_history=True,
        )
        repeat_two = export_library(
            "acme-fork",
            actor=self.actor,
            mode="effective_snapshot",
            acknowledge_retained_history=True,
        )
        self.assertEqual(repeat_one.semantic_digest, repeat_two.semantic_digest)
        self.assertEqual(repeat_one.document, repeat_two.document)
