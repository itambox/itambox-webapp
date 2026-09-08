"""T22 pure contracts for the browser-facing Type Library forms."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from assets.library_forms import (
    LibraryApplyForm,
    LibraryExportForm,
    LibraryUploadForm,
    deserialize_library_plan,
    serialize_library_plan,
)
from assets.services.type_library.planning import LibraryPlan, LibraryPlanAction
from assets.views.library_views import TypeLibraryImportView


def _plan(*, can_apply: bool = False) -> LibraryPlan:
    conflict = LibraryPlanAction(
        action_id="sha256:conflict-action",
        action="conflict",
        identity="acme/device-a",
        path=("definitions", "asset_types", "acme/device-a", "model"),
        baseline="Device",
        local="Local Device",
        incoming="Upstream Device",
        decision="abort" if not can_apply else "keep_local",
        reason="three_way_conflict",
    )
    unchanged = LibraryPlanAction(
        action_id="sha256:unchanged-action",
        action="unchanged",
        identity="acme/device-a",
        path=("definitions", "asset_types", "acme/device-a", "description"),
        baseline="same",
        local="same",
        incoming="same",
        decision="unchanged",
        reason="unchanged",
    )
    return LibraryPlan(
        namespace="acme",
        incoming_release=2,
        source_digest="sha256:source",
        snapshot_digest=None,
        baseline_digest="sha256:baseline",
        current_digest="sha256:current",
        actions=(conflict, unchanged),
        conflicts=(conflict,),
        resolutions=((conflict.action_id, conflict.decision),),
        can_apply=can_apply,
        plan_digest="sha256:plan",
    )


class T22LibraryPureFormTests(SimpleTestCase):
    def test_library_routes_are_registered_in_production(self):
        for name, kwargs in (
            ("type_library_list", {}),
            ("type_library_import", {}),
            ("type_library_detail", {"pk": 1}),
            ("type_library_export", {"pk": 1}),
        ):
            with self.subTest(name=name):
                url = reverse(f"assets:{name}", kwargs=kwargs)
                self.assertEqual(resolve(url).view_name, f"assets:{name}")

    def test_invalid_plan_is_a_form_error_not_an_unhandled_exception(self):
        view = TypeLibraryImportView()
        view.request = RequestFactory().post("/", {"action": "apply", "plan_payload": "invalid"})
        with patch.object(view, "_render_import", side_effect=lambda **kwargs: kwargs):
            result = view._post_apply_workflow("apply")
        self.assertIn("plan_payload", result["apply_form"].errors)

    def test_plan_payload_round_trip_preserves_original_signed_request_plan(self):
        plan = _plan(can_apply=False)

        restored = deserialize_library_plan(serialize_library_plan(plan))

        self.assertEqual(restored, plan)
        self.assertEqual(restored.resolutions, (("sha256:conflict-action", "abort"),))

    def test_apply_form_exposes_only_contract_resolution_choices_and_preserves_source(self):
        plan = _plan(can_apply=False)
        form = LibraryApplyForm(
            data={
                "source_document": json.dumps({"library": {"namespace": "acme"}}),
                "plan_payload": serialize_library_plan(plan),
                "preview_token": "signed-preview-token",
                "resolution_sha256_conflict_action": "take_upstream",
            },
            conflicts=plan.conflicts,
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["source_document"], '{"library": {"namespace": "acme"}}')
        self.assertEqual(form.resolutions(), {"sha256:conflict-action": "take_upstream"})
        self.assertEqual(
            [value for value, _label in form.fields["resolution_sha256_conflict_action"].choices],
            ["keep_local", "take_upstream", "abort"],
        )

    def test_upload_form_rejects_a_payload_over_the_library_safety_envelope(self):
        upload = SimpleUploadedFile("library.json", b"{}", content_type="application/json")
        upload.size = 10 * 1024 * 1024 + 1

        form = LibraryUploadForm(files={"document": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("10 MiB", str(form.errors["document"]))

    def test_export_form_requires_a_distinct_namespace_for_fork_mode(self):
        form = LibraryExportForm(data={"mode": "fork", "new_namespace": "acme"}, current_namespace="acme")

        self.assertFalse(form.is_valid())
        self.assertIn("different namespace", str(form.errors["new_namespace"]))

    def test_export_form_accepts_original_and_snapshot_without_fork_namespace(self):
        for mode in ("original_release", "effective_snapshot"):
            with self.subTest(mode=mode):
                form = LibraryExportForm(data={"mode": mode})
                self.assertTrue(form.is_valid(), form.errors)

    def test_upload_form_reads_a_real_file_object_without_replacing_the_stream(self):
        raw = b'{"schema_version":1,"kind":"itambox.type-library.release"}'
        form = LibraryUploadForm(
            files={"document": SimpleUploadedFile("library.json", raw, content_type="application/json")}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["document"].read(), raw)


@pytest.mark.parametrize("decision", ("keep_local", "take_upstream", "abort"))
def test_serialized_resolution_values_are_json_safe(decision):
    plan = _plan()
    payload = json.loads(serialize_library_plan(plan))
    payload["resolutions"] = {"sha256:conflict-action": decision}

    restored = deserialize_library_plan(json.dumps(payload))

    assert restored.resolutions == (("sha256:conflict-action", decision),)
    assert restored.can_apply is False
