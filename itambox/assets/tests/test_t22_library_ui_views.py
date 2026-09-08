"""T22 real route/browser-facing workflow tests."""

from __future__ import annotations

import copy
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from assets.api.tests.test_type_library_http import _snapshot_rows
from assets.models import AssetType
from assets.tests.test_t17_type_library_validation import _release_document
from extras.models import CustomField, SpecificationLibrary


class T22LibraryBrowserWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_superuser(
            username="t22-library-admin",
            email="t22-library-admin@example.invalid",
            password="not-used-in-test-client",
        )

    def setUp(self):
        self.client.force_login(self.actor)

    @staticmethod
    def _document(*, release: int = 1, label: str | None = None) -> dict[str, object]:
        document = copy.deepcopy(_release_document())
        document["library"]["release"] = release  # type: ignore[index]
        if label is not None:
            document["definitions"]["fields"][0]["label"] = label  # type: ignore[index]
        return document

    def _upload(self, document: dict[str, object]):
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return self.client.post(
            reverse("assets:type_library_import"),
            {
                "document": SimpleUploadedFile(
                    "library.json",
                    payload,
                    content_type="application/json",
                )
            },
        )

    def _apply_preview(self, response, *, resolutions: dict[str, str] | None = None):
        form = response.context["apply_form"]
        self.assertIsNotNone(form)
        data = {field.name: field.value() for field in form.hidden_fields()}
        for name, field in form.fields.items():
            if name.startswith("resolution_"):
                data[name] = (resolutions or {}).get(name, field.initial or "abort")
        data["action"] = "apply"
        return self.client.post(reverse("assets:type_library_import"), data)

    def test_real_upload_preview_apply_routes_and_global_readback(self):
        preview = self._upload(self._document())
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Preview is not an apply.")
        self.assertContains(preview, "Apply Library")
        self.assertFalse(SpecificationLibrary.objects.exists())

        applied = self._apply_preview(preview)
        self.assertEqual(applied.status_code, 200)
        self.assertContains(applied, "The Library was applied successfully.")
        self.assertContains(applied, "data-library-apply-result")
        library = SpecificationLibrary.objects.get(namespace="acme")
        self.assertIsNotNone(library.accepted_release_id)
        self.assertEqual(library.releases.count(), 1)

        listing = self.client.get(reverse("assets:type_library_list"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "acme")
        detail = self.client.get(reverse("assets:type_library_detail", kwargs={"pk": library.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Immutable release history")
        self.assertContains(detail, "Tenant Asset values")

    def test_export_modes_and_fork_reimport_are_real_commands(self):
        self._apply_preview(self._upload(self._document()))
        library = SpecificationLibrary.objects.get(namespace="acme")
        export_url = reverse("assets:type_library_export", kwargs={"pk": library.pk})

        original = self.client.post(export_url, {"mode": "original_release"})
        self.assertEqual(original.status_code, 200)
        original_document = json.loads(original.content)
        self.assertEqual(original_document["kind"], "itambox.type-library.release")
        self.assertIn("original_release", original["Content-Disposition"])

        snapshot = self.client.post(
            export_url,
            {"mode": "effective_snapshot", "acknowledge_retained_history": "on"},
        )
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(json.loads(snapshot.content)["kind"], "itambox.type-library.snapshot")
        self.assertIn("effective_snapshot", snapshot["Content-Disposition"])

        fork = self.client.post(export_url, {"mode": "fork", "new_namespace": "acme-fork"})
        self.assertEqual(fork.status_code, 200)
        fork_document = json.loads(fork.content)
        self.assertEqual(fork_document["library"]["namespace"], "acme-fork")
        self.assertIn("fork", fork["Content-Disposition"])

        fork_preview = self.client.post(
            reverse("assets:type_library_import"),
            {
                "document": SimpleUploadedFile(
                    "fork.json",
                    fork.content,
                    content_type="application/json",
                )
            },
        )
        self.assertEqual(fork_preview.status_code, 200)
        self.assertEqual(fork_preview.context["preview"].plan.namespace, "acme-fork")
        fork_apply = self._apply_preview(fork_preview)
        self.assertEqual(fork_apply.status_code, 200)
        self.assertContains(fork_apply, "The Library was applied successfully.")
        self.assertTrue(SpecificationLibrary.objects.filter(namespace="acme-fork").exists())

    def test_blocking_conflict_explains_choices_and_takes_upstream(self):
        self._resolve_conflict("take_upstream")

    def test_blocking_conflict_keeps_local_value_after_apply(self):
        self._resolve_conflict("keep_local")

    def _resolve_conflict(self, decision):
        self._apply_preview(self._upload(self._document()))
        library = SpecificationLibrary.objects.get(namespace="acme")
        snapshot_response = self.client.post(
            reverse("assets:type_library_export", kwargs={"pk": library.pk}),
            {"mode": "effective_snapshot", "acknowledge_retained_history": "on"},
        )
        local_snapshot = json.loads(snapshot_response.content)
        state_field = next(
            field for field in local_snapshot["effective_definitions"]["fields"] if field["key"] == "acme__state"
        )
        state_field["label"] = "Local State"
        local_snapshot["effective_definitions"]["asset_types"][0]["specifications"]["acme__capacity"] = "24"
        local_preview = self._upload(local_snapshot)
        self.assertEqual(local_preview.context["preview"].plan.can_apply, True)
        local_apply = self._apply_preview(local_preview)
        self.assertContains(local_apply, "The Library was applied successfully.")
        self.assertEqual(CustomField.objects.get(namespace="acme", name="acme__state").label, "Local State")
        self.assertEqual(
            next(
                field["label"]
                for field in SpecificationLibrary.objects.get(namespace="acme").accepted_release.source_document[
                    "definitions"
                ]["fields"]
                if field["key"] == "acme__state"
            ),
            "State",
        )

        upstream = self._document(release=2, label="Upstream State")
        upstream["definitions"]["asset_types"][0]["specifications"]["acme__capacity"] = "64"
        conflict_preview = self._upload(upstream)
        self.assertEqual(conflict_preview.status_code, 200)
        plan = conflict_preview.context["preview"].plan
        self.assertTrue(plan.conflicts)
        self.assertFalse(plan.can_apply)
        self.assertContains(conflict_preview, "Blocking conflicts require a decision.")
        self.assertContains(conflict_preview, "keep_local")
        self.assertContains(conflict_preview, "take_upstream")
        self.assertContains(conflict_preview, "abort")
        self.assertContains(conflict_preview, "data-library-apply")
        self.assertContains(conflict_preview, "disabled")

        conflict_form = conflict_preview.context["apply_form"]
        resolution_data = {field.name: field.value() for field in conflict_form.hidden_fields()}
        for name in conflict_form.fields:
            if name.startswith("resolution_"):
                resolution_data[name] = decision
        resolution_data["action"] = "review_resolutions"
        resolved_preview = self.client.post(reverse("assets:type_library_import"), resolution_data)
        self.assertTrue(resolved_preview.context["preview"].plan.can_apply)
        applied = self._apply_preview(resolved_preview)
        self.assertFalse(applied.context["result"].no_op)
        self.assertContains(applied, "The Library was applied successfully.")
        self.assertEqual(
            CustomField.objects.get(namespace="acme", name="acme__state").label,
            "Local State" if decision == "keep_local" else "Upstream State",
        )
        updated_type = AssetType.objects.get(model="Device", part_number="A")
        self.assertEqual(updated_type.custom_field_data["acme__capacity"], "24" if decision == "keep_local" else "64")
        accepted = SpecificationLibrary.objects.get(namespace="acme").accepted_release
        self.assertEqual(accepted.sequence, 2)
        self.assertEqual(
            next(
                field["label"]
                for field in accepted.source_document["definitions"]["fields"]
                if field["key"] == "acme__state"
            ),
            "Upstream State",
        )
        before_reimport = _snapshot_rows()
        reimport = self._apply_preview(self._upload(upstream))
        self.assertTrue(reimport.context["result"].no_op)
        self.assertEqual(_snapshot_rows(), before_reimport)

    def test_stale_apply_refreshes_preview_and_retains_original_source(self):
        self._apply_preview(self._upload(self._document()))
        source = self._document(release=2)
        preview = self._upload(source)
        self.assertEqual(preview.context["preview"].plan.can_apply, True)

        field = CustomField.objects.get(namespace="acme", name="acme__state")
        CustomField.objects.filter(pk=field.pk).update(label="Concurrent local edit")
        stale = self._apply_preview(preview)

        self.assertEqual(stale.status_code, 200)
        self.assertContains(stale, "Preview refreshed")
        self.assertEqual(stale.context["preview"].plan.incoming_release, 2)
        self.assertEqual(stale.context["apply_form"].initial["source_document"], json.dumps(source, ensure_ascii=False))
        self.assertEqual(SpecificationLibrary.objects.get(namespace="acme").accepted_release.sequence, 1)

    def test_change_permission_is_checked_before_detail_lookup(self):
        self.client.logout()
        actor = get_user_model().objects.create_user(username="t22-read-only", password="unused")
        self.client.force_login(actor)
        response = self.client.get(reverse("assets:type_library_detail", kwargs={"pk": 999999}))
        self.assertEqual(response.status_code, 403)
