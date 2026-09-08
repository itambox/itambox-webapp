"""Focused PostgreSQL T18-T20 transactional library qualification."""

from __future__ import annotations

import json
from copy import deepcopy

from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.services.type_library.application import LibraryApplyRequest
from assets.services.type_library.commands import apply_library, export_library, preview_library
from assets.services.type_library.writing import effective_definitions_from_library
from assets.services.type_library_validation import validate_library_document
from assets.tests.test_t17_type_library_validation import _release_document
from extras.models import CustomFieldChoice, SpecificationLibrary, SpecificationLibraryRelease
from organization.services.access_scope import authentication_revision_for_actor


class LibraryTransactionalWriteTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="library-transaction-test",
            password=None,
        )
        self.signing_key = b"library-transaction-test-key"

    def _validated(self, document):
        return validate_library_document(json.dumps(document, ensure_ascii=False))

    def _apply(self, incoming):
        preview = preview_library(
            incoming.canonical_bytes,
            actor=self.actor,
            signing_key=self.signing_key,
        )
        authentication_revision = authentication_revision_for_actor(self.actor)
        return apply_library(
            incoming.canonical_bytes,
            LibraryApplyRequest(
                plan=preview.plan,
                token=preview.preview_token,
                actor_id=self.actor.pk,
                authentication_revision=authentication_revision,
                access_scope_fingerprint=None,
                signing_key=self.signing_key,
            ),
            actor=self.actor,
        )

    def test_fresh_apply_and_retained_choice_round_trip_use_real_transactional_models(self):
        first_document = _release_document()
        first_document["definitions"]["choice_sets"][0]["choices"].append(  # type: ignore[index]
            {"key": "legacy", "label": "Legacy", "lifecycle": "deprecated"}
        )
        first = self._validated(first_document)

        first_result = self._apply(first)

        library = SpecificationLibrary.objects.get(namespace="acme")
        self.assertEqual(first_result.release, 1)
        self.assertEqual(library.accepted_release.sequence, 1)
        self.assertEqual(SpecificationLibraryRelease.objects.filter(library=library).count(), 1)
        legacy = CustomFieldChoice.objects.get(choice_set__namespace="acme", key="legacy")
        self.assertEqual(legacy.lifecycle, "deprecated")
        self.assertIsNotNone(legacy.deprecated_at)

        second_document = deepcopy(first_document)
        second_document["library"]["release"] = 2  # type: ignore[index]
        choices = second_document["definitions"]["choice_sets"][0]["choices"]  # type: ignore[index]
        second_document["definitions"]["choice_sets"][0]["choices"] = [  # type: ignore[index]
            choice for choice in choices if choice["key"] != "off"
        ]
        second_document["definitions"]["asset_types"][0]["specifications"]["acme__state"] = ["on"]  # type: ignore[index]
        second = self._validated(second_document)
        second_result = self._apply(second)

        library.refresh_from_db()
        self.assertEqual(second_result.release, 2)
        self.assertEqual(library.accepted_release.sequence, 2)
        self.assertEqual(SpecificationLibraryRelease.objects.filter(library=library).count(), 2)
        retired = CustomFieldChoice.objects.get(choice_set__namespace="acme", key="off")
        self.assertEqual(retired.lifecycle, "deprecated")
        self.assertIsNotNone(retired.deprecated_at)

        effective = effective_definitions_from_library(library)
        effective_choices = {
            choice["key"]: choice
            for choice in effective["choice_sets"][0]["choices"]
        }
        self.assertEqual(effective_choices["legacy"]["lifecycle"], "deprecated")
        self.assertEqual(effective_choices["off"]["lifecycle"], "deprecated")

        original = export_library("acme", actor=self.actor, mode="original_release")
        self.assertEqual(original.mode, "original_release")
        self.assertEqual(original.document["library"]["release"], 2)
        effective_export = export_library(
            "acme",
            actor=self.actor,
            mode="effective_snapshot",
            acknowledge_retained_history=True,
        )
        self.assertEqual(effective_export.mode, "effective_snapshot")
        self.assertEqual(
            effective_export.document["effective_definitions"]["choice_sets"][0]["choices"][-1]["lifecycle"],
            "deprecated",
        )
        fork = export_library("acme", actor=self.actor, mode="fork", new_namespace="acme-fork")
        self.assertEqual(fork.mode, "fork")
        self.assertTrue(fork.identity_changed)
        self.assertEqual(fork.namespace, "acme-fork")
