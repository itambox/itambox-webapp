"""REST transport for the Type Library preview/apply/export lifecycle.

This module is intentionally an adapter only.  It validates transport syntax,
constructs the real typed LibraryApplyRequest, and delegates document validation,
planning, authorization, locking, persistence, and export provenance to the
canonical Type Library commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_field, extend_schema_view
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assets.services.specifications.preview_tokens import MAX_PREVIEW_TOKEN_LENGTH
from assets.services.type_library.application import (
    LibraryApplyError,
    LibraryApplyRequest,
    LibraryApplyResult,
)
from assets.services.type_library.commands import (
    LibraryCommandError,
    apply_library,
    export_library,
    preview_library,
)
from assets.services.type_library.exporting import LibraryExportArtifact, LibraryExportError
from assets.services.type_library.planning import LibraryPlan, LibraryPlanAction
from assets.services.type_library_validation.limits import ValidationLimits
from organization.services.access_scope import authentication_revision_for_actor

MAX_LIBRARY_DOCUMENT_BYTES = ValidationLimits().max_bytes
MAX_PLAN_ACTIONS = 20_000
MAX_PLAN_PATH_PARTS = 64
_LIBRARY_NAMESPACE_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
_PLAN_ACTIONS = ("create", "update", "unchanged", "deprecate", "conflict", "reference")
_PLAN_DECISIONS = ("unchanged", "take_upstream", "keep_local", "abort", "conflict")
_RESOLUTION_CHOICES = ("keep_local", "take_upstream", "abort")

_ERROR_MESSAGES = {
    "REFERENCE_CONFLICT": "The submitted library conflicts with existing state.",
    "OWNERSHIP_CONFLICT": "The submitted library is not owned by this scope.",
    "DEPENDENCY_RETIREMENT": "A required library dependency cannot be retired.",
    "UNSUPPORTED_STRUCTURE": "The submitted library structure is not supported.",
    "STALE_PLAN": "The preview plan is no longer valid.",
    "OBJECT_UNAVAILABLE": "The requested library is unavailable.",
    "EXPORT_BLOCKED": "The requested export is blocked.",
    "INVALID_RESOLUTION": "The conflict resolution is invalid.",
    "CONFLICT": "The preview contains unresolved conflicts.",
}
_STALE_CODES = frozenset({"STALE_RESOURCE", "STALE_DEFINITION", "STALE_PLAN"})
_CONFLICT_CODES = frozenset(
    {
        "CONFLICT",
        "EQUIVOCATION",
        "EXPORT_BLOCKED",
        "OWNERSHIP_CONFLICT",
        "REFERENCE_CONFLICT",
        "DEPENDENCY_RETIREMENT",
        "UNSUPPORTED_STRUCTURE",
    }
)


@extend_schema_field(OpenApiTypes.STR)
class BoundedJSONDocumentField(serializers.Field[bytes]):
    """Accept only bounded UTF-8 text or an uploaded file's raw bytes."""

    default_error_messages = {
        "invalid_type": "Expected UTF-8 JSON document text or an uploaded file.",
        "invalid_utf8": "The document is not valid UTF-8.",
        "resource_limit": "The document exceeds the 10 MiB size limit.",
        "read_failed": "The uploaded document could not be read.",
    }

    def _read_document_bytes(self, data: object) -> bytes:
        if isinstance(data, UploadedFile):
            try:
                raw = data.read(MAX_LIBRARY_DOCUMENT_BYTES + 1)
            except (OSError, ValueError) as exc:
                raise serializers.ValidationError(self.error_messages["read_failed"], code="read_failed") from exc
            if not isinstance(raw, bytes):
                self.fail("invalid_type")
        elif type(data) is str:
            if len(data) > MAX_LIBRARY_DOCUMENT_BYTES:
                self.fail("resource_limit")
            try:
                raw = data.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise serializers.ValidationError(self.error_messages["invalid_utf8"], code="invalid_utf8") from exc
        elif type(data) is bytes:
            raw = data
        else:
            self.fail("invalid_type")
        return raw

    def to_internal_value(self, data: object) -> bytes:
        raw = self._read_document_bytes(data)
        if len(raw) > MAX_LIBRARY_DOCUMENT_BYTES:
            self.fail("resource_limit")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise serializers.ValidationError(self.error_messages["invalid_utf8"], code="invalid_utf8") from exc
        return raw


class StrictLibrarySerializer(serializers.Serializer):
    """Reject unknown request members instead of silently dropping them."""

    def to_internal_value(self, data: object) -> object:
        if isinstance(data, Mapping):
            _reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        initial = getattr(self, "initial_data", None)
        if isinstance(initial, Mapping):
            _reject_unknown_fields(initial, self.fields)
        return attrs


def _reject_unknown_fields(
    submitted: Mapping[str, object],
    fields: Mapping[str, serializers.Field[Any]],
) -> None:
    unknown = sorted(set(submitted) - set(fields))
    if unknown:
        raise serializers.ValidationError({name: "Unknown field." for name in unknown})


class ResolutionInputField(serializers.DictField):
    """Map deterministic action IDs to one adopted conflict decision."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            child=serializers.ChoiceField(choices=_RESOLUTION_CHOICES),
            allow_empty=True,
            **kwargs,
        )

    def to_internal_value(self, data: object) -> dict[str, str]:
        value = super().to_internal_value(data)
        if any(type(key) is not str or not key for key in value):
            raise serializers.ValidationError("Resolution keys must be non-empty action IDs.", code="invalid")
        return value


class LibraryPreviewInputSerializer(StrictLibrarySerializer):
    document = BoundedJSONDocumentField(required=True)
    resolutions = ResolutionInputField(required=False, default=dict)


class LibraryPlanActionInputSerializer(StrictLibrarySerializer):
    action_id = serializers.CharField(max_length=256, allow_blank=False)
    action = serializers.ChoiceField(choices=_PLAN_ACTIONS)
    identity = serializers.CharField(max_length=256, allow_blank=False)
    path = serializers.ListField(
        child=serializers.CharField(max_length=256, allow_blank=False),
        allow_empty=False,
        max_length=MAX_PLAN_PATH_PARTS,
    )
    baseline = serializers.JSONField(required=False, allow_null=True, default=None)
    local = serializers.JSONField(required=False, allow_null=True, default=None)
    incoming = serializers.JSONField(required=False, allow_null=True, default=None)
    decision = serializers.ChoiceField(choices=_PLAN_DECISIONS)
    reason = serializers.CharField(max_length=256, allow_blank=False)


class LibraryPlanInputSerializer(StrictLibrarySerializer):
    namespace = serializers.RegexField(_LIBRARY_NAMESPACE_PATTERN, max_length=62)
    incoming_release = serializers.IntegerField(min_value=1)
    source_digest = serializers.CharField(max_length=128, allow_blank=False)
    snapshot_digest = serializers.CharField(max_length=128, allow_blank=False, required=False, allow_null=True)
    baseline_digest = serializers.CharField(max_length=128, allow_blank=False, required=False, allow_null=True)
    current_digest = serializers.CharField(max_length=128, allow_blank=False, required=False, allow_null=True)
    actions = LibraryPlanActionInputSerializer(many=True, allow_empty=True, max_length=MAX_PLAN_ACTIONS)
    conflicts = LibraryPlanActionInputSerializer(many=True, allow_empty=True, max_length=MAX_PLAN_ACTIONS)
    resolutions = ResolutionInputField(required=False, default=dict)
    can_apply = serializers.BooleanField()
    plan_digest = serializers.CharField(max_length=128, allow_blank=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        actions = attrs["actions"]
        conflicts = attrs["conflicts"]
        if not isinstance(actions, list) or not isinstance(conflicts, list):
            return attrs
        action_ids = [str(action["action_id"]) for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise serializers.ValidationError({"actions": "Action IDs must be unique."})
        expected_conflicts = [action for action in actions if action["action"] == "conflict"]
        supplied_conflicts = [action for action in conflicts]
        if supplied_conflicts != expected_conflicts:
            raise serializers.ValidationError({"conflicts": "Conflicts must match the plan's conflict actions."})
        conflict_ids = {str(action["action_id"]) for action in expected_conflicts}
        resolutions = attrs.get("resolutions", {})
        if isinstance(resolutions, dict):
            unknown = sorted(set(resolutions) - conflict_ids)
            if unknown:
                raise serializers.ValidationError({"resolutions": "A resolution does not identify a plan conflict."})
            mismatched = sorted(
                action_id
                for action_id, decision in resolutions.items()
                if next(action for action in expected_conflicts if action["action_id"] == action_id)["decision"]
                != decision
            )
            if mismatched:
                raise serializers.ValidationError(
                    {"resolutions": "Resolutions must match the signed conflict decisions."}
                )
        expected_can_apply = not any(
            action["action"] == "conflict" and action["decision"] in {"abort", "conflict"} for action in actions
        )
        if attrs["can_apply"] is not expected_can_apply:
            raise serializers.ValidationError({"can_apply": "can_apply does not match the signed plan actions."})
        return attrs


class LibraryApplyInputSerializer(StrictLibrarySerializer):
    document = BoundedJSONDocumentField(required=True)
    preview_token = serializers.CharField(max_length=MAX_PREVIEW_TOKEN_LENGTH, allow_blank=False)
    plan = LibraryPlanInputSerializer(required=True)
    resolutions = ResolutionInputField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        if "resolutions" in attrs:
            plan = attrs.get("plan")
            if isinstance(plan, Mapping) and attrs["resolutions"] != plan.get("resolutions", {}):
                raise serializers.ValidationError(
                    {"resolutions": [ErrorDetail("The resolutions do not match the signed plan.", code="stale_plan")]}
                )
        return attrs


class LibraryExportInputSerializer(StrictLibrarySerializer):
    namespace = serializers.RegexField(_LIBRARY_NAMESPACE_PATTERN, max_length=62)
    mode = serializers.ChoiceField(choices=("original_release", "effective_snapshot", "fork"))
    new_namespace = serializers.RegexField(_LIBRARY_NAMESPACE_PATTERN, max_length=62, required=False)
    acknowledge_retained_history = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        mode = attrs["mode"]
        new_namespace = attrs.get("new_namespace")
        if mode == "fork" and not new_namespace:
            raise serializers.ValidationError({"new_namespace": "A fork requires a new namespace."})
        if mode != "fork" and new_namespace is not None:
            raise serializers.ValidationError({"new_namespace": "Only a fork accepts new_namespace."})
        return attrs


class LibraryValidatedDocumentOutputSerializer(serializers.Serializer):
    kind = serializers.CharField(read_only=True)
    semantic_digest = serializers.CharField(read_only=True)


class LibraryIssueOutputSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    path = serializers.ListField(child=serializers.JSONField(), read_only=True)
    field_key = serializers.CharField(read_only=True, allow_null=True, required=False)
    message = serializers.CharField(read_only=True)


class LibraryPlanActionOutputSerializer(serializers.Serializer):
    action_id = serializers.CharField(read_only=True)
    action = serializers.ChoiceField(choices=_PLAN_ACTIONS, read_only=True)
    identity = serializers.CharField(read_only=True)
    path = serializers.ListField(child=serializers.CharField(), read_only=True)
    baseline = serializers.JSONField(read_only=True, allow_null=True, required=False)
    local = serializers.JSONField(read_only=True, allow_null=True, required=False)
    incoming = serializers.JSONField(read_only=True, allow_null=True, required=False)
    decision = serializers.ChoiceField(choices=_PLAN_DECISIONS, read_only=True)
    reason = serializers.CharField(read_only=True)


class LibraryPlanOutputSerializer(serializers.Serializer):
    namespace = serializers.CharField(read_only=True)
    incoming_release = serializers.IntegerField(read_only=True)
    source_digest = serializers.CharField(read_only=True)
    snapshot_digest = serializers.CharField(read_only=True, allow_null=True, required=False)
    baseline_digest = serializers.CharField(read_only=True, allow_null=True, required=False)
    current_digest = serializers.CharField(read_only=True, allow_null=True, required=False)
    actions = LibraryPlanActionOutputSerializer(many=True, read_only=True)
    conflicts = LibraryPlanActionOutputSerializer(many=True, read_only=True)
    resolutions = ResolutionInputField(read_only=True)
    can_apply = serializers.BooleanField(read_only=True)
    plan_digest = serializers.CharField(read_only=True)


class LibraryPreviewResponseSerializer(serializers.Serializer):
    preview_token = serializers.CharField(read_only=True)
    plan = LibraryPlanOutputSerializer(read_only=True)
    can_apply = serializers.BooleanField(read_only=True)
    document = LibraryValidatedDocumentOutputSerializer(read_only=True)


class LibraryApplyResponseSerializer(serializers.Serializer):
    namespace = serializers.CharField(read_only=True)
    release = serializers.IntegerField(read_only=True)
    plan_digest = serializers.CharField(read_only=True)
    source_digest = serializers.CharField(read_only=True)
    changed_action_ids = serializers.ListField(child=serializers.CharField(), read_only=True)
    no_op = serializers.BooleanField(read_only=True)


class LibraryErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    issues = LibraryIssueOutputSerializer(many=True, read_only=True)


class LibraryErrorResponseSerializer(serializers.Serializer):
    error = LibraryErrorBodySerializer(read_only=True)


class LibraryExportResponseSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=("original_release", "effective_snapshot", "fork"),
        read_only=True,
    )
    namespace = serializers.CharField(read_only=True)
    source_digest = serializers.CharField(read_only=True)
    semantic_digest = serializers.CharField(read_only=True)
    identity_changed = serializers.BooleanField(read_only=True)
    document = serializers.JSONField(read_only=True)


def _decode_plan(data: Mapping[str, object]) -> LibraryPlan:
    actions_data = data["actions"]
    if not isinstance(actions_data, list):
        raise TypeError("validated plan actions must be a list")
    actions = tuple(_decode_action(action) for action in actions_data)
    resolutions_data = data.get("resolutions", {})
    if not isinstance(resolutions_data, Mapping):
        raise TypeError("validated plan resolutions must be a mapping")
    resolutions = tuple(sorted((str(key), str(value)) for key, value in resolutions_data.items()))
    conflicts = tuple(action for action in actions if action.action == "conflict")
    return LibraryPlan(
        namespace=str(data["namespace"]),
        incoming_release=int(data["incoming_release"]),
        source_digest=str(data["source_digest"]),
        snapshot_digest=_optional_string(data.get("snapshot_digest")),
        baseline_digest=_optional_string(data.get("baseline_digest")),
        current_digest=_optional_string(data.get("current_digest")),
        actions=actions,
        conflicts=conflicts,
        resolutions=resolutions,
        can_apply=bool(data["can_apply"]),
        plan_digest=str(data["plan_digest"]),
    )


def _decode_action(data: object) -> LibraryPlanAction:
    if not isinstance(data, Mapping):
        raise TypeError("validated plan action must be a mapping")
    path = data["path"]
    if not isinstance(path, list):
        raise TypeError("validated plan action path must be a list")
    return LibraryPlanAction(
        action_id=str(data["action_id"]),
        action=str(data["action"]),
        identity=str(data["identity"]),
        path=tuple(str(part) for part in path),
        baseline=data.get("baseline"),
        local=data.get("local"),
        incoming=data.get("incoming"),
        decision=str(data["decision"]),
        reason=str(data["reason"]),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _server_signing_key() -> str:
    """Return the configured server key; transport never accepts one from input."""

    key = getattr(settings, "SECRET_KEY", None)
    if not isinstance(key, str) or not key:
        raise RuntimeError("a configured server signing key is required")
    return key


def _authentication_revision(actor: object) -> str:
    """Resolve the current actor revision from the trusted server-side actor."""

    return str(authentication_revision_for_actor(actor))


def _preview_command(
    document: bytes,
    *,
    actor: object,
    signing_key: str,
    resolutions: Mapping[str, str],
) -> object:
    """Call the stable command seam without requiring an empty optional kwarg."""

    kwargs: dict[str, object] = {
        "actor": actor,
        "signing_key": signing_key,
        "access_scope_fingerprint": None,
    }
    if resolutions:
        # The repaired core command accepts explicit conflict decisions.  The
        # committed no-decision surface remains callable while that successor is
        # being integrated; no adapter-side planner is used as a fallback.
        kwargs["resolutions"] = resolutions
    return preview_library(document, **kwargs)


def _preview_payload(result: object) -> dict[str, object]:
    plan = result.plan
    validated = result.validated
    return {
        "preview_token": str(result.preview_token),
        "plan": _plan_payload(plan),
        "can_apply": bool(plan.can_apply),
        "document": {
            "kind": str(validated.kind),
            "semantic_digest": str(validated.semantic_digest),
        },
    }


def _plan_payload(plan: LibraryPlan) -> dict[str, object]:
    return {
        "namespace": plan.namespace,
        "incoming_release": plan.incoming_release,
        "source_digest": plan.source_digest,
        "snapshot_digest": plan.snapshot_digest,
        "baseline_digest": plan.baseline_digest,
        "current_digest": plan.current_digest,
        "actions": [_action_payload(action) for action in plan.actions],
        "conflicts": [_action_payload(action) for action in plan.conflicts],
        "resolutions": {key: value for key, value in plan.resolutions},
        "can_apply": plan.can_apply,
        "plan_digest": plan.plan_digest,
    }


def _action_payload(action: LibraryPlanAction) -> dict[str, object]:
    return {
        "action_id": action.action_id,
        "action": action.action,
        "identity": action.identity,
        "path": list(action.path),
        "baseline": action.baseline,
        "local": action.local,
        "incoming": action.incoming,
        "decision": action.decision,
        "reason": action.reason,
    }


def _apply_payload(result: LibraryApplyResult) -> dict[str, object]:
    return {
        "namespace": result.namespace,
        "release": result.release,
        "plan_digest": result.plan_digest,
        "source_digest": result.source_digest,
        "changed_action_ids": list(result.changed_action_ids),
        "no_op": result.no_op,
    }


def _export_payload(artifact: LibraryExportArtifact) -> dict[str, object]:
    return {
        "mode": artifact.mode,
        "namespace": artifact.namespace,
        "source_digest": artifact.source_digest,
        "semantic_digest": artifact.semantic_digest,
        "identity_changed": artifact.identity_changed,
        "document": artifact.document,
    }


def _serializer_error_response(errors: Mapping[str, object]) -> Response:
    issues = tuple(_flatten_serializer_errors(errors))
    return _error_response(issues)


def _flatten_serializer_errors(value: object, path: tuple[str | int, ...] = ()) -> list[dict[str, object]]:
    if isinstance(value, Mapping):
        issues: list[dict[str, object]] = []
        for key, nested in value.items():
            issues.extend(_flatten_serializer_errors(nested, path + (str(key),)))
        return issues
    if isinstance(value, list):
        issues = []
        for index, nested in enumerate(value):
            issues.extend(_flatten_serializer_errors(nested, path + (index,)))
        return issues
    code = str(getattr(value, "code", "invalid")).upper()
    return [{"code": _transport_error_code(code), "path": list(path), "field_key": None, "message": str(value)}]


def _transport_error_code(code: str) -> str:
    return {
        "INVALID": "INVALID_TYPE",
        "INVALID_TYPE": "INVALID_TYPE",
        "RESOURCE_LIMIT": "RESOURCE_LIMIT",
        "INVALID_UTF8": "INVALID_TYPE",
        "STALE_PLAN": "STALE_PLAN",
    }.get(code, code)


def _command_error_response(exc: LibraryCommandError | LibraryApplyError | LibraryExportError) -> Response:
    raw_issues = getattr(exc, "issues", ())
    if raw_issues:
        issues = tuple(
            {
                "code": str(issue.code),
                "path": [part for part in issue.path],
                "field_key": None,
                "message": str(issue.message),
            }
            for issue in raw_issues
        )
    else:
        code = str(getattr(exc, "code", "UNSUPPORTED_STRUCTURE"))
        path = tuple(getattr(exc, "path", ()))
        message = str(getattr(exc, "message", None) or str(exc) or code)
        issues = ({"code": code, "path": list(path), "field_key": None, "message": message},)
    return _error_response(issues)


def _error_response(issues: Sequence[Mapping[str, object]]) -> Response:
    normalized = tuple(issues) or (
        {"code": "UNSUPPORTED_STRUCTURE", "path": [], "field_key": None, "message": "The request is invalid."},
    )
    codes = {str(issue["code"]) for issue in normalized}
    first_code = str(normalized[0]["code"])
    if "OBJECT_UNAVAILABLE" in codes:
        response_status = status.HTTP_404_NOT_FOUND
    elif codes.intersection(_STALE_CODES):
        response_status = status.HTTP_412_PRECONDITION_FAILED
    elif "RESOURCE_LIMIT" in codes:
        response_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    elif codes.intersection(_CONFLICT_CODES):
        response_status = status.HTTP_409_CONFLICT
    else:
        response_status = status.HTTP_400_BAD_REQUEST
    return Response(
        {
            "error": {
                "code": first_code,
                "message": _ERROR_MESSAGES.get(first_code, str(normalized[0]["message"])),
                "issues": list(normalized),
            }
        },
        status=response_status,
    )


@extend_schema_view(
    post=extend_schema(
        operation_id="assets_type_library_preview",
        tags=["Type Library"],
        request=LibraryPreviewInputSerializer,
        responses={
            status.HTTP_200_OK: LibraryPreviewResponseSerializer,
            status.HTTP_400_BAD_REQUEST: LibraryErrorResponseSerializer,
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
            status.HTTP_409_CONFLICT: LibraryErrorResponseSerializer,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: LibraryErrorResponseSerializer,
        },
    )
)
class TypeLibraryPreviewAPIView(APIView):
    """Preview a bounded release/snapshot without persistent writes."""

    permission_classes = (IsAuthenticated,)

    def post(self, request, *args: object, **kwargs: object) -> Response:
        serializer = LibraryPreviewInputSerializer(data=request.data)
        if not serializer.is_valid():
            return _serializer_error_response(serializer.errors)
        values = serializer.validated_data
        try:
            result = _preview_command(
                values["document"],
                actor=request.user,
                signing_key=_server_signing_key(),
                resolutions=values["resolutions"],
            )
        except (LibraryCommandError, LibraryApplyError, LibraryExportError) as exc:
            return _command_error_response(exc)
        return Response(_preview_payload(result), status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        operation_id="assets_type_library_apply",
        tags=["Type Library"],
        request=LibraryApplyInputSerializer,
        responses={
            status.HTTP_200_OK: LibraryApplyResponseSerializer,
            status.HTTP_400_BAD_REQUEST: LibraryErrorResponseSerializer,
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
            status.HTTP_404_NOT_FOUND: LibraryErrorResponseSerializer,
            status.HTTP_409_CONFLICT: LibraryErrorResponseSerializer,
            status.HTTP_412_PRECONDITION_FAILED: LibraryErrorResponseSerializer,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: LibraryErrorResponseSerializer,
        },
    )
)
class TypeLibraryApplyAPIView(APIView):
    """Apply exactly the client-supplied signed plan and preconditions."""

    permission_classes = (IsAuthenticated,)

    def post(self, request, *args: object, **kwargs: object) -> Response:
        serializer = LibraryApplyInputSerializer(data=request.data)
        if not serializer.is_valid():
            return _serializer_error_response(serializer.errors)
        values = serializer.validated_data
        actor_id = getattr(request.user, "pk", None)
        if type(actor_id) is not int or actor_id <= 0:
            return _error_response(
                ({"code": "OBJECT_UNAVAILABLE", "path": [], "field_key": None, "message": "Actor unavailable"},)
            )
        try:
            apply_request = LibraryApplyRequest(
                plan=_decode_plan(values["plan"]),
                token=str(values["preview_token"]),
                actor_id=actor_id,
                authentication_revision=_authentication_revision(request.user),
                access_scope_fingerprint=None,
                signing_key=_server_signing_key(),
            )
            result = apply_library(values["document"], apply_request, actor=request.user)
        except (LibraryCommandError, LibraryApplyError, LibraryExportError) as exc:
            return _command_error_response(exc)
        return Response(_apply_payload(result), status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        operation_id="assets_type_library_export",
        tags=["Type Library"],
        request=LibraryExportInputSerializer,
        responses={
            status.HTTP_200_OK: LibraryExportResponseSerializer,
            status.HTTP_400_BAD_REQUEST: LibraryErrorResponseSerializer,
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
            status.HTTP_404_NOT_FOUND: LibraryErrorResponseSerializer,
            status.HTTP_409_CONFLICT: LibraryErrorResponseSerializer,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: LibraryErrorResponseSerializer,
        },
    )
)
class TypeLibraryExportAPIView(APIView):
    """Export an authorized original release, effective snapshot, or fork."""

    permission_classes = (IsAuthenticated,)

    def post(self, request, *args: object, **kwargs: object) -> Response:
        serializer = LibraryExportInputSerializer(data=request.data)
        if not serializer.is_valid():
            return _serializer_error_response(serializer.errors)
        values = serializer.validated_data
        try:
            artifact = export_library(
                values["namespace"],
                actor=request.user,
                mode=values["mode"],
                new_namespace=values.get("new_namespace"),
                acknowledge_retained_history=values["acknowledge_retained_history"],
            )
        except (LibraryCommandError, LibraryApplyError, LibraryExportError) as exc:
            return _command_error_response(exc)
        return Response(_export_payload(artifact), status=status.HTTP_200_OK)


# Parent/integrator registration proposal.  Kept here so URL/OpenAPI ownership
# remains explicit without modifying the shared router in this worker.
TYPE_LIBRARY_ROUTE_REGISTRATION = (
    ("type-libraries/preview/", TypeLibraryPreviewAPIView, "type-library-preview"),
    ("type-libraries/apply/", TypeLibraryApplyAPIView, "type-library-apply"),
    ("type-libraries/export/", TypeLibraryExportAPIView, "type-library-export"),
)


__all__ = [
    "MAX_LIBRARY_DOCUMENT_BYTES",
    "LibraryApplyInputSerializer",
    "LibraryExportInputSerializer",
    "LibraryPlanInputSerializer",
    "LibraryPreviewInputSerializer",
    "TYPE_LIBRARY_ROUTE_REGISTRATION",
    "TypeLibraryApplyAPIView",
    "TypeLibraryExportAPIView",
    "TypeLibraryPreviewAPIView",
]
