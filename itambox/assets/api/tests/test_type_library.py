"""Pure transport contract tests for the Type Library REST adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from assets.api.type_library import (
    MAX_LIBRARY_DOCUMENT_BYTES,
    LibraryApplyInputSerializer,
    LibraryPreviewInputSerializer,
    TypeLibraryApplyAPIView,
    TypeLibraryExportAPIView,
    TypeLibraryPreviewAPIView,
    _decode_plan,
)
from assets.services.type_library.application import LibraryApplyResult
from assets.services.type_library.commands import LibraryCommandError
from assets.services.type_library.exporting import LibraryExportArtifact


def _actor():
    return SimpleNamespace(pk=17, is_authenticated=True)


def _plan_payload(*, resolutions=None, can_apply=True):
    return {
        "namespace": "acme",
        "incoming_release": 1,
        "source_digest": "sha256:source",
        "snapshot_digest": None,
        "baseline_digest": None,
        "current_digest": None,
        "actions": [],
        "conflicts": [],
        "resolutions": {} if resolutions is None else resolutions,
        "can_apply": can_apply,
        "plan_digest": "sha256:plan",
    }


def _preview_result():
    return SimpleNamespace(
        preview_token="signed-preview-token",
        plan=SimpleNamespace(
            namespace="acme",
            incoming_release=1,
            source_digest="sha256:source",
            snapshot_digest=None,
            baseline_digest=None,
            current_digest=None,
            actions=(),
            conflicts=(),
            resolutions=(),
            can_apply=True,
            plan_digest="sha256:plan",
        ),
        validated=SimpleNamespace(kind="itambox.type-library.release", semantic_digest="sha256:source"),
    )


def test_preview_input_is_strict_and_requires_utf8_json_text():
    valid = LibraryPreviewInputSerializer(data={"document": '{"schema_version": 1}', "resolutions": {}})
    assert valid.is_valid(), valid.errors
    assert valid.validated_data["document"] == b'{"schema_version": 1}'

    unknown = LibraryPreviewInputSerializer(data={"document": "{}", "actor": 17, "signing_key": "client-controlled"})
    assert not unknown.is_valid()
    assert set(unknown.errors) == {"actor", "signing_key"}

    mapping = LibraryPreviewInputSerializer(data={"document": {"schema_version": 1}})
    assert not mapping.is_valid()
    assert "document" in mapping.errors


def test_document_upload_is_bounded_before_command_validation():
    oversized = SimpleUploadedFile("library.json", b"x" * (MAX_LIBRARY_DOCUMENT_BYTES + 1))
    serializer = LibraryPreviewInputSerializer(data={"document": oversized})

    assert not serializer.is_valid()
    assert serializer.errors["document"][0].code == "resource_limit"

    invalid_utf8 = SimpleUploadedFile("library.json", b"\xff")
    serializer = LibraryPreviewInputSerializer(data={"document": invalid_utf8})
    assert not serializer.is_valid()
    assert serializer.errors["document"][0].code == "invalid_utf8"


def test_oversized_invalid_unicode_is_rejected_before_encoding():
    with patch("assets.api.type_library.MAX_LIBRARY_DOCUMENT_BYTES", 4):
        invalid_unicode = "xxxxx" + chr(0xD800)
        serializer = LibraryPreviewInputSerializer(data={"document": invalid_unicode})
        assert not serializer.is_valid()

    assert serializer.errors["document"][0].code == "resource_limit"


def test_oversized_text_uses_the_early_character_bound():
    with patch("assets.api.type_library.MAX_LIBRARY_DOCUMENT_BYTES", 4):
        serializer = LibraryPreviewInputSerializer(data={"document": "xxxxx"})
        assert not serializer.is_valid()

    assert serializer.errors["document"][0].code == "resource_limit"


def test_multibyte_text_still_uses_the_encoded_byte_bound():
    with patch("assets.api.type_library.MAX_LIBRARY_DOCUMENT_BYTES", 4):
        serializer = LibraryPreviewInputSerializer(data={"document": "ééé"})
        assert not serializer.is_valid()

    assert serializer.errors["document"][0].code == "resource_limit"


def test_apply_plan_decoder_rejects_resolution_that_is_not_bound_to_a_conflict():
    payload = _plan_payload(resolutions={"sha256:unknown": "keep_local"})
    serializer = LibraryApplyInputSerializer(data={"document": "{}", "preview_token": "signed", "plan": payload})

    assert not serializer.is_valid()
    assert "plan" in serializer.errors or "resolutions" in serializer.errors


def test_preview_forwards_server_context_and_explicit_resolutions():
    factory = APIRequestFactory()
    request = factory.post(
        "/api/assets/type-libraries/preview/",
        {
            "document": '{"schema_version": 1}',
            "resolutions": {"sha256:action": "take_upstream"},
        },
        format="json",
    )
    actor = _actor()
    force_authenticate(request, user=actor)
    command = Mock(return_value=_preview_result())

    with (
        patch("assets.api.type_library.preview_library", command),
        patch("assets.api.type_library._server_signing_key", return_value="server-key"),
    ):
        response = TypeLibraryPreviewAPIView.as_view()(request)

    assert response.status_code == status.HTTP_200_OK
    command.assert_called_once()
    args, kwargs = command.call_args
    assert args == (b'{"schema_version": 1}',)
    assert kwargs["actor"] is actor
    assert kwargs["signing_key"] == "server-key"
    assert kwargs["access_scope_fingerprint"] is None
    assert kwargs["resolutions"] == {"sha256:action": "take_upstream"}
    assert "actor" not in response.data
    assert "signing_key" not in response.data


def test_preview_without_decisions_keeps_committed_command_signature_compatible():
    request = APIRequestFactory().post(
        "/api/assets/type-libraries/preview/",
        {"document": '{"schema_version": 1}'},
        format="json",
    )
    force_authenticate(request, user=_actor())
    command = Mock(return_value=_preview_result())

    with (
        patch("assets.api.type_library.preview_library", command),
        patch("assets.api.type_library._server_signing_key", return_value="server-key"),
    ):
        response = TypeLibraryPreviewAPIView.as_view()(request)

    assert response.status_code == status.HTTP_200_OK
    kwargs = command.call_args.kwargs
    assert kwargs["access_scope_fingerprint"] is None
    assert "resolutions" not in kwargs


def test_apply_uses_the_client_plan_and_token_without_silent_repreview():
    plan_payload = _plan_payload()
    request = APIRequestFactory().post(
        "/api/assets/type-libraries/apply/",
        {
            "document": '{"schema_version": 1}',
            "preview_token": "signed-preview-token",
            "plan": plan_payload,
        },
        format="json",
    )
    actor = _actor()
    force_authenticate(request, user=actor)
    command = Mock(
        return_value=LibraryApplyResult(
            namespace="acme",
            release=1,
            plan_digest="sha256:plan",
            source_digest="sha256:source",
            changed_action_ids=(),
            no_op=True,
        )
    )
    preview = Mock()

    with (
        patch("assets.api.type_library.apply_library", command),
        patch("assets.api.type_library.preview_library", preview),
        patch("assets.api.type_library._server_signing_key", return_value="server-key"),
        patch("assets.api.type_library._authentication_revision", return_value="auth-revision"),
    ):
        response = TypeLibraryApplyAPIView.as_view()(request)

    assert response.status_code == status.HTTP_200_OK
    preview.assert_not_called()
    command.assert_called_once()
    args, kwargs = command.call_args
    assert args[0] == b'{"schema_version": 1}'
    apply_request = args[1]
    assert apply_request.token == "signed-preview-token"
    assert apply_request.plan.plan_digest == "sha256:plan"
    assert apply_request.actor_id == actor.pk
    assert apply_request.authentication_revision == "auth-revision"
    assert apply_request.access_scope_fingerprint is None
    assert apply_request.signing_key == "server-key"
    assert kwargs["actor"] is actor


def test_apply_rejects_top_level_resolution_changes_instead_of_replacing_signed_plan():
    plan = _plan_payload()
    serializer = LibraryApplyInputSerializer(
        data={
            "document": "{}",
            "preview_token": "signed-preview-token",
            "plan": plan,
            "resolutions": {"sha256:changed": "keep_local"},
        }
    )
    assert not serializer.is_valid()
    assert serializer.errors["resolutions"][0].code == "stale_plan"


def test_export_forwards_only_adopted_mode_and_namespace_fields():
    request = APIRequestFactory().post(
        "/api/assets/type-libraries/export/",
        {
            "namespace": "acme",
            "mode": "fork",
            "new_namespace": "acme-local",
            "acknowledge_retained_history": True,
        },
        format="json",
    )
    actor = _actor()
    force_authenticate(request, user=actor)
    artifact = LibraryExportArtifact(
        mode="fork",
        document={"schema_version": 1, "kind": "itambox.type-library.release"},
        canonical_bytes=b"{}",
        semantic_digest="sha256:effective",
        source_digest="sha256:source",
        namespace="acme-local",
        identity_changed=True,
    )
    command = Mock(return_value=artifact)

    with patch("assets.api.type_library.export_library", command):
        response = TypeLibraryExportAPIView.as_view()(request)

    assert response.status_code == status.HTTP_200_OK
    command.assert_called_once_with(
        "acme",
        actor=actor,
        mode="fork",
        new_namespace="acme-local",
        acknowledge_retained_history=True,
    )
    assert response.data["document"]["kind"] == "itambox.type-library.release"
    assert response.data["identity_changed"] is True


def test_command_errors_are_structured_and_preserve_paths():
    request = APIRequestFactory().post(
        "/api/assets/type-libraries/preview/",
        {"document": "{}"},
        format="json",
    )
    force_authenticate(request, user=_actor())

    with (
        patch(
            "assets.api.type_library.preview_library",
            side_effect=LibraryCommandError("REFERENCE_CONFLICT", ("definitions", "fields", 0)),
        ),
        patch("assets.api.type_library._server_signing_key", return_value="server-key"),
    ):
        response = TypeLibraryPreviewAPIView.as_view()(request)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data == {
        "error": {
            "code": "REFERENCE_CONFLICT",
            "message": "The submitted library conflicts with existing state.",
            "issues": [
                {
                    "code": "REFERENCE_CONFLICT",
                    "path": ["definitions", "fields", 0],
                    "field_key": None,
                    "message": "The submitted library conflicts with existing state.",
                }
            ],
        }
    }


def test_plan_decoder_returns_a_typed_plan_not_a_client_dataclass():
    serializer = LibraryApplyInputSerializer(
        data={"document": "{}", "preview_token": "signed", "plan": _plan_payload()}
    )
    assert serializer.is_valid(), serializer.errors

    plan = _decode_plan(serializer.validated_data["plan"])
    assert plan.namespace == "acme"
    assert plan.actions == ()
    assert plan.resolutions == ()
    assert plan.can_apply is True
    assert json.dumps(serializer.validated_data["plan"], sort_keys=True)


def test_drf_spectacular_annotations_describe_the_dedicated_routes():
    from drf_spectacular.generators import SchemaGenerator

    from core.urls import urlpatterns

    schema = SchemaGenerator(patterns=urlpatterns).get_schema(request=None, public=True)
    for path in (
        "/api/assets/type-libraries/preview/",
        "/api/assets/type-libraries/apply/",
        "/api/assets/type-libraries/export/",
    ):
        operation = schema["paths"][path]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert operation["responses"]["400"]["content"]["application/json"]["schema"]
