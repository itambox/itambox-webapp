"""Provenance-preserving Type Library release/snapshot/fork exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from assets.services.type_library.planning import LibraryReconciliationState
from assets.services.type_library_validation import (
    LibraryValidationError,
    ValidatedLibraryDocument,
    validate_library_document,
)
from assets.services.type_library_validation.errors import ValidationIssue

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MUTABLE_FIELD_KEYS = frozenset({"label", "help_text", "lifecycle"})
_MUTABLE_TYPE_KEYS = frozenset(
    {"description", "configuration", "specifications", "historical_specifications", "lifecycle"}
)
_SHARED_SECTIONS = frozenset({"categories", "manufacturers"})


class LibraryExportError(ValueError):
    """A precise, transport-neutral export failure."""

    def __init__(
        self,
        code: str,
        path: tuple[str | int, ...] = (),
        message: str | None = None,
        *,
        issues: tuple[ValidationIssue, ...] = (),
    ):
        self.code = code
        self.path = path
        self.issues = issues or (ValidationIssue(code, path, message or code),)
        super().__init__("; ".join(str(issue) for issue in self.issues))


@dataclass(frozen=True, slots=True)
class LibraryExportArtifact:
    """Canonical export plus explicit provenance and identity metadata."""

    mode: str
    document: dict[str, Any]
    canonical_bytes: bytes
    semantic_digest: str
    source_digest: str
    namespace: str
    identity_changed: bool


def export_original_release(
    release: ValidatedLibraryDocument | Mapping[str, Any],
    *,
    installed_dependencies: Mapping[Any, Any] | None = None,
) -> LibraryExportArtifact:
    """Return the retained immutable source document, never current local state."""

    validated = _validated_release(release, installed_dependencies=installed_dependencies)
    return _artifact(
        mode="original_release",
        validated=validated,
        source_digest=validated.semantic_digest,
        identity_changed=False,
    )


def export_effective_snapshot(
    release: ValidatedLibraryDocument | Mapping[str, Any],
    effective_definitions: Mapping[str, Any],
    *,
    installed_dependencies: Mapping[Any, Any] | None = None,
    acknowledge_retained_history: bool = False,
) -> LibraryExportArtifact:
    """Export source plus current effective definitions as a snapshot.

    Only the definitions graph is accepted.  Tenant Asset values, identifiers,
    assignments, audit state, and installation policy therefore cannot enter
    this artifact through this API.
    """

    validated_release = _validated_release(release, installed_dependencies=installed_dependencies)
    if not isinstance(effective_definitions, Mapping):
        raise LibraryExportError("SCHEMA_TYPE", ("effective_definitions",), "Expected an object")
    effective = deepcopy(dict(effective_definitions))
    _check_snapshot_identity(
        validated_release.normalized_document,
        effective,
        acknowledge_retained_history=acknowledge_retained_history,
    )
    snapshot = {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(validated_release.normalized_document),
        "effective_definitions": effective,
    }
    validated_snapshot = _validate_mapping(snapshot, installed_dependencies=installed_dependencies)
    return _artifact(
        mode="effective_snapshot",
        validated=validated_snapshot,
        source_digest=validated_release.semantic_digest,
        identity_changed=False,
    )


def export_fork(
    release: ValidatedLibraryDocument | Mapping[str, Any],
    *,
    new_namespace: str,
    installed_dependencies: Mapping[Any, Any] | None = None,
) -> LibraryExportArtifact:
    """Publish a deliberate new namespace with rewritten owned identities."""

    validated_release = _validated_release(release, installed_dependencies=installed_dependencies)
    old_namespace = _library_namespace(validated_release.normalized_document)
    if not isinstance(new_namespace, str) or _NAMESPACE_RE.fullmatch(new_namespace) is None:
        raise LibraryExportError("INVALID_NAMESPACE", ("library", "namespace"), "Invalid fork namespace")
    if new_namespace == old_namespace:
        raise LibraryExportError("IDENTITY_UNCHANGED", ("library", "namespace"), "A fork needs a new namespace")
    fork = _rewrite_fork(validated_release.normalized_document, old_namespace, new_namespace)
    validated_fork = _validate_mapping(fork, installed_dependencies=installed_dependencies)
    return _artifact(
        mode="fork",
        validated=validated_fork,
        source_digest=validated_release.semantic_digest,
        identity_changed=True,
    )


def load_library_state(library: Any) -> LibraryReconciliationState:
    """Load the accepted baseline and current exportable state for apply.

    The model serializer is intentionally imported lazily to keep validation,
    planning, and export tests database-free.  It is the sole production state
    loader used by ``apply_library_plan``.
    """

    from assets.services.type_library.writing import effective_definitions_from_library

    accepted = library.accepted_release
    baseline = None if accepted is None else deepcopy(accepted.source_document)
    effective = None if baseline is None else {
        **deepcopy(baseline),
        "definitions": effective_definitions_from_library(library),
    }
    return LibraryReconciliationState(
        namespace=library.namespace,
        accepted_release=None if accepted is None else accepted.sequence,
        baseline_document=baseline,
        effective_document=effective,
    )


def _artifact(
    *,
    mode: str,
    validated: ValidatedLibraryDocument,
    source_digest: str,
    identity_changed: bool,
) -> LibraryExportArtifact:
    return LibraryExportArtifact(
        mode=mode,
        document=deepcopy(validated.normalized_document),
        canonical_bytes=validated.canonical_bytes,
        semantic_digest=validated.semantic_digest,
        source_digest=source_digest,
        namespace=_library_namespace(validated.normalized_document),
        identity_changed=identity_changed,
    )


def _validated_release(
    document: ValidatedLibraryDocument | Mapping[str, Any],
    *,
    installed_dependencies: Mapping[Any, Any] | None,
) -> ValidatedLibraryDocument:
    validated = document if isinstance(document, ValidatedLibraryDocument) else _validate_mapping(
        document,
        installed_dependencies=installed_dependencies,
    )
    if validated.kind != "itambox.type-library.release":
        raise LibraryExportError("INVALID_EXPORT_KIND", ("kind",), "Original/export source must be a release")
    return validated


def _validate_mapping(
    document: Mapping[str, Any],
    *,
    installed_dependencies: Mapping[Any, Any] | None,
) -> ValidatedLibraryDocument:
    try:
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return validate_library_document(encoded, installed_dependencies=installed_dependencies)
    except LibraryValidationError as exc:
        raise LibraryExportError(exc.code, exc.path, issues=exc.issues) from exc
    except (TypeError, ValueError) as exc:
        raise LibraryExportError("INVALID_EXPORT", (), str(exc)) from exc


def _check_snapshot_identity(
    source: Mapping[str, Any],
    effective: Mapping[str, Any],
    *,
    acknowledge_retained_history: bool,
) -> None:
    retained_paths: list[tuple[str | int, ...]] = []
    source_namespace = _library_namespace(source)
    for section, source_items in source.get("definitions", {}).items():
        retained_paths.extend(
            _check_snapshot_section(
                section,
                source_items,
                effective.get(section, []),
                source_namespace,
            )
        )
    if retained_paths and not acknowledge_retained_history:
        raise LibraryExportError(
            "RETAINED_HISTORY_ACK_REQUIRED",
            retained_paths[0],
            "Retained historical definitions require explicit acknowledgement",
        )


def _check_snapshot_section(
    section: str,
    source_items: list[Mapping[str, Any]],
    effective_items: list[Mapping[str, Any]],
    source_namespace: str,
) -> list[tuple[str | int, ...]]:
    source_by_id = {_definition_identity(section, item): item for item in source_items}
    effective_by_id = {_definition_identity(section, item): item for item in effective_items}
    for identity, source_item in source_by_id.items():
        _require_snapshot_source_item(section, identity, source_item, effective_by_id)
    retained_paths = _new_snapshot_definition_paths(
        section,
        source_by_id,
        effective_by_id,
        source_namespace,
    )
    if section == "choice_sets":
        _check_choice_history(source_by_id, effective_by_id, retained_paths)
    return retained_paths


def _require_snapshot_source_item(
    section: str,
    identity: str,
    source_item: Mapping[str, Any],
    effective_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    effective_item = effective_by_id.get(identity)
    if effective_item is None:
        raise LibraryExportError(
            "SNAPSHOT_MISSING_DEFINITION",
            ("effective_definitions", section, identity),
            "Upstream-owned definitions cannot be omitted",
        )
    allowed_choice_keys = {choice["key"] for choice in source_item.get("choices", [])}
    if _structural_projection(
        section,
        source_item,
        allowed_choice_keys=allowed_choice_keys,
    ) != _structural_projection(
        section,
        effective_item,
        allowed_choice_keys=allowed_choice_keys,
    ):
        raise LibraryExportError(
            "SNAPSHOT_STRUCTURAL_MISMATCH",
            ("effective_definitions", section, identity),
            "Snapshot changed immutable source structure",
        )


def _new_snapshot_definition_paths(
    section: str,
    source_by_id: Mapping[str, Mapping[str, Any]],
    effective_by_id: Mapping[str, Mapping[str, Any]],
    source_namespace: str,
) -> list[tuple[str | int, ...]]:
    retained_paths: list[tuple[str | int, ...]] = []
    for identity, effective_item in effective_by_id.items():
        if identity in source_by_id:
            continue
        lifecycle = effective_item.get("lifecycle")
        namespace = _item_namespace(section, effective_item)
        if lifecycle == "deprecated" and namespace == source_namespace:
            retained_paths.append(("effective_definitions", section, identity))
            continue
        if section not in _SHARED_SECTIONS or namespace == source_namespace:
            raise LibraryExportError(
                "SNAPSHOT_IDENTITY_CONFLICT",
                ("effective_definitions", section, identity),
                "Active new definitions need an explicit fork namespace",
            )
    return retained_paths


def _check_choice_history(
    source_by_id: Mapping[str, Mapping[str, Any]],
    effective_by_id: Mapping[str, Mapping[str, Any]],
    retained_paths: list[tuple[str | int, ...]],
) -> None:
    for identity, source_item in source_by_id.items():
        effective_item = effective_by_id.get(identity, {})
        source_choices = {choice["key"] for choice in source_item.get("choices", [])}
        for choice in effective_item.get("choices", []):
            if choice["key"] not in source_choices and choice.get("lifecycle") == "deprecated":
                retained_paths.append(("effective_definitions", "choice_sets", identity, "choices", choice["key"]))
            elif choice["key"] not in source_choices:
                raise LibraryExportError(
                    "SNAPSHOT_IDENTITY_CONFLICT",
                    ("effective_definitions", "choice_sets", identity, "choices", choice["key"]),
                    "Active new Choices need an explicit fork namespace",
                )


def _structural_projection(
    section: str,
    item: Mapping[str, Any],
    *,
    allowed_choice_keys: set[str] | None = None,
) -> Any:
    projected = deepcopy(dict(item))
    if section == "fields":
        for key in _MUTABLE_FIELD_KEYS:
            projected.pop(key, None)
    elif section == "asset_types":
        for key in _MUTABLE_TYPE_KEYS:
            projected.pop(key, None)
    elif section in _SHARED_SECTIONS:
        projected.pop("label", None)
        projected.pop("description", None)
        projected.pop("lifecycle", None)
    elif section == "choice_sets":
        projected["choices"] = [
            choice["key"]
            for choice in projected.get("choices", [])
            if allowed_choice_keys is None or choice["key"] in allowed_choice_keys
        ]
        projected.pop("label", None)
        projected.pop("description", None)
        projected.pop("lifecycle", None)
    elif section == "fieldsets":
        projected.pop("label", None)
        projected.pop("description", None)
        projected.pop("lifecycle", None)
    return projected


def _definition_identity(section: str, item: Mapping[str, Any]) -> str:
    if isinstance(item.get("id"), str):
        return item["id"]
    if section == "fields":
        return f"{item.get('namespace')}/{item.get('key')}"
    return str(item.get("key", ""))


def _item_namespace(section: str, item: Mapping[str, Any]) -> str | None:
    if section == "fields":
        return item.get("namespace") if isinstance(item.get("namespace"), str) else None
    identity = item.get("id")
    if isinstance(identity, str) and "/" in identity:
        return identity.split("/", 1)[0]
    return None


def _library_namespace(document: Mapping[str, Any]) -> str:
    if document.get("kind") == "itambox.type-library.snapshot":
        document = document["upstream"]
    return document["library"]["namespace"]


def _rewrite_fork(document: Mapping[str, Any], old_namespace: str, new_namespace: str) -> dict[str, Any]:
    fork = deepcopy(dict(document))
    fork["library"]["namespace"] = new_namespace
    definitions = fork["definitions"]
    for section, items in definitions.items():
        for item in items:
            if section == "fields":
                item["namespace"] = new_namespace
                item["key"] = _rewrite_field_key(item["key"], old_namespace, new_namespace)
            elif section not in _SHARED_SECTIONS:
                item["id"] = _rewrite_owned_reference(item["id"], old_namespace, new_namespace)
            _rewrite_nested_references(section, item, old_namespace, new_namespace)
    return fork


def _rewrite_nested_references(section: str, item: dict[str, Any], old_namespace: str, new_namespace: str) -> None:
    for key in ("choice_set", "category", "manufacturer"):
        if key in item and key == "choice_set":
            item[key] = _rewrite_owned_reference(item[key], old_namespace, new_namespace)
    for key in ("fields", "fieldsets", "default_fieldsets"):
        if key in item:
            item[key] = [_rewrite_owned_reference(value, old_namespace, new_namespace) for value in item[key]]
    if section == "asset_types":
        item["specifications"] = {
            _rewrite_field_key(key, old_namespace, new_namespace): value
            for key, value in item.get("specifications", {}).items()
        }
        item["historical_specifications"] = {
            _rewrite_field_key(key, old_namespace, new_namespace): value
            for key, value in item.get("historical_specifications", {}).items()
        }


def _rewrite_owned_reference(value: str, old_namespace: str, new_namespace: str) -> str:
    prefix = f"{old_namespace}/"
    if not value.startswith(prefix):
        return value
    tail = value[len(prefix) :]
    if "__" in tail:
        tail = _rewrite_field_key(tail, old_namespace, new_namespace)
    return f"{new_namespace}/{tail}"


def _rewrite_field_key(value: str, old_namespace: str, new_namespace: str) -> str:
    old_prefix = f"{old_namespace.replace('-', '_')}__"
    new_prefix = f"{new_namespace.replace('-', '_')}__"
    return f"{new_prefix}{value[len(old_prefix):]}" if value.startswith(old_prefix) else value


__all__ = [
    "LibraryExportArtifact",
    "LibraryExportError",
    "export_effective_snapshot",
    "export_fork",
    "export_original_release",
    "load_library_state",
]
