"""Pure deterministic reconciliation planner for Type Library v1.

The planner consumes a validated normalized document and caller-supplied
baseline/current snapshots.  It performs no database work, authorization, or
writes.  Lists are atomic values; identity and path ordering are explicit so a
plan is stable under irrelevant JSON member/definition ordering.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from assets.services.specifications.preview_tokens import (
    PreviewTokenExpectation,
    issue_preview_token,
    normalized_input_digest,
    verify_preview_token,
)
from assets.services.type_library_validation.validation import ValidatedLibraryDocument
from extras.canonicalization import canonicalize_release_document

PlanActionKind = Literal["create", "update", "unchanged", "deprecate", "conflict", "reference"]
Resolution = Literal["unchanged", "take_upstream", "keep_local", "abort"]
_MISSING = object()
_SHARED_SECTIONS = frozenset({"categories", "manufacturers"})
_STRUCTURAL_FIELD_KEYS = frozenset(
    {
        "namespace",
        "key",
        "targets",
        "activation",
        "field_type",
        "required",
        "nullable",
        "validation",
        "quantity_kind",
        "canonical_unit",
        "choice_set",
    }
)


class LibraryPlanningError(ValueError):
    """A deterministic planning or token-binding failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(code)
        self.message = message


@dataclass(frozen=True, slots=True)
class LibraryReconciliationState:
    """The accepted upstream baseline and the current effective local state."""

    namespace: str
    accepted_release: int | None
    baseline_document: Mapping[str, Any] | None
    effective_document: Mapping[str, Any] | None

    @property
    def baseline_digest(self) -> str | None:
        if self.baseline_document is None:
            return None
        return _source_digest(self.baseline_document)

    @property
    def current_digest(self) -> str | None:
        if self.effective_document is None:
            return None
        return _effective_digest(self.effective_document)


@dataclass(frozen=True, slots=True)
class LibraryPlanAction:
    """One exact object/path decision in a reconciliation plan."""

    action_id: str
    action: PlanActionKind
    identity: str
    path: tuple[str, ...]
    baseline: Any
    local: Any
    incoming: Any
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class LibraryPlan:
    """Complete deterministic plan and its state bindings."""

    namespace: str
    incoming_release: int
    source_digest: str
    snapshot_digest: str | None
    baseline_digest: str | None
    current_digest: str | None
    actions: tuple[LibraryPlanAction, ...]
    conflicts: tuple[LibraryPlanAction, ...]
    resolutions: tuple[tuple[str, str], ...]
    can_apply: bool
    plan_digest: str

    @property
    def expected_current_digest(self) -> str | None:
        return self.current_digest


@dataclass(frozen=True, slots=True)
class LibraryTokenBinding:
    """All non-secret values bound into a library preview token."""

    namespace: str
    source_digest: str
    snapshot_digest: str | None
    baseline_digest: str | None
    current_digest: str | None
    plan_digest: str
    resolutions: tuple[tuple[str, str], ...]

    def normalized_input(self) -> dict[str, Any]:
        return {
            "version": 1,
            "namespace": self.namespace,
            "source_digest": self.source_digest,
            "snapshot_digest": self.snapshot_digest,
            "baseline_digest": self.baseline_digest,
            "current_digest": self.current_digest,
            "plan_digest": self.plan_digest,
            "resolutions": {key: value for key, value in self.resolutions},
        }

    @property
    def input_digest(self) -> str:
        return normalized_input_digest(self.normalized_input())


def plan_reconciliation(
    state: LibraryReconciliationState,
    incoming: ValidatedLibraryDocument,
    *,
    resolutions: Mapping[str, str] | None = None,
) -> LibraryPlan:
    """Build a deterministic B/L/R plan from a validated document."""

    if not isinstance(state, LibraryReconciliationState):
        raise TypeError("state must be a LibraryReconciliationState")
    if not isinstance(incoming, ValidatedLibraryDocument):
        raise TypeError("incoming must be a ValidatedLibraryDocument")
    normalized_resolutions = dict(resolutions or {})
    source_document, incoming_effective, incoming_release, snapshot_digest = _incoming_parts(incoming)
    namespace = _validate_release_boundary(state, source_document, incoming_release)
    _reject_structural_field_changes(state.baseline_document, incoming_effective)
    source_digest = _source_digest(source_document)
    actions = _build_reconciliation_actions(state, incoming_effective, normalized_resolutions)
    reference_action = _reference_action(state.baseline_document, source_document)
    if reference_action is not None:
        actions.append(reference_action)

    actions.sort(key=lambda item: (item.path, item.action_id))
    resolution_items = tuple(sorted((str(key), str(value)) for key, value in normalized_resolutions.items()))
    conflicts = tuple(action for action in actions if action.action == "conflict")
    can_apply = not any(action.decision in {"abort", "conflict"} for action in conflicts)
    plan_digest = _plan_digest(
        namespace=namespace,
        incoming_release=incoming_release,
        source_digest=source_digest,
        snapshot_digest=snapshot_digest,
        baseline_digest=state.baseline_digest,
        current_digest=state.current_digest,
        actions=actions,
        resolutions=resolution_items,
    )
    return LibraryPlan(
        namespace=namespace,
        incoming_release=incoming_release,
        source_digest=source_digest,
        snapshot_digest=snapshot_digest,
        baseline_digest=state.baseline_digest,
        current_digest=state.current_digest,
        actions=tuple(actions),
        conflicts=conflicts,
        resolutions=resolution_items,
        can_apply=can_apply,
        plan_digest=plan_digest,
    )


def _validate_release_boundary(
    state: LibraryReconciliationState,
    source_document: Mapping[str, Any],
    incoming_release: int,
) -> str:
    namespace = _library_namespace(source_document)
    if namespace != state.namespace:
        raise LibraryPlanningError("OWNERSHIP_CONFLICT", "incoming library namespace does not match the target")
    if state.accepted_release is not None and incoming_release < state.accepted_release:
        raise LibraryPlanningError("OLDER_RELEASE", "an older library release cannot be planned")
    source_digest = _source_digest(source_document)
    if (
        state.accepted_release is not None
        and incoming_release == state.accepted_release
        and state.baseline_digest is not None
        and state.baseline_digest != source_digest
    ):
        raise LibraryPlanningError("EQUIVOCATION", "the same library release has a different source digest")
    return namespace


def _reject_structural_field_changes(
    baseline: Mapping[str, Any] | None,
    incoming_effective: Mapping[str, Any],
) -> None:
    baseline_fields = {
        identity: item for (section, identity), item in _entity_map(baseline).items() if section == "fields"
    }
    incoming_fields = {
        identity: item for (section, identity), item in _entity_map(incoming_effective).items() if section == "fields"
    }
    for identity in sorted(set(baseline_fields) & set(incoming_fields)):
        baseline_field = baseline_fields[identity]
        incoming_field = incoming_fields[identity]
        for key in _STRUCTURAL_FIELD_KEYS:
            if not _equal(baseline_field.get(key, _MISSING), incoming_field.get(key, _MISSING)):
                raise LibraryPlanningError(
                    "UNSUPPORTED_STRUCTURE",
                    f"field {identity} structural path {key} changed",
                )


def _build_reconciliation_actions(
    state: LibraryReconciliationState,
    incoming_effective: Mapping[str, Any],
    resolutions: Mapping[str, str],
) -> list[LibraryPlanAction]:
    baseline_entities = _entity_map(state.baseline_document)
    local_entities = _entity_map(state.effective_document)
    incoming_entities = _entity_map(incoming_effective)
    keys = set(baseline_entities) | set(local_entities) | set(incoming_entities)
    actions: list[LibraryPlanAction] = []
    for section, identity in sorted(keys, key=lambda item: (item[0], item[1])):
        actions.extend(
            _entity_actions(
                section,
                identity,
                baseline_entities.get((section, identity), _MISSING),
                local_entities.get((section, identity), _MISSING),
                incoming_entities.get((section, identity), _MISSING),
                resolutions,
            )
        )
    return actions


def _entity_actions(
    section: str,
    identity: str,
    baseline: Mapping[str, Any] | object,
    local: Mapping[str, Any] | object,
    remote: Mapping[str, Any] | object,
    resolutions: Mapping[str, str],
) -> list[LibraryPlanAction]:
    entity_path = ("definitions", section, identity)
    if baseline is _MISSING:
        if remote is _MISSING:
            return []
        if local is not _MISSING:
            return [
                _action(
                    "conflict",
                    identity,
                    entity_path,
                    None,
                    local,
                    remote,
                    decision="abort",
                    reason="local_unmanaged_identity_collision",
                )
            ]
        return [
            _action(
                "reference" if section in _SHARED_SECTIONS else "create",
                identity,
                entity_path,
                None,
                None,
                remote,
                decision="take_upstream",
                reason="incoming_identity",
            )
        ]
    if remote is _MISSING:
        if section in _SHARED_SECTIONS:
            return []
        return [
            _action(
                "deprecate",
                identity,
                entity_path,
                baseline,
                local if local is not _MISSING else None,
                None,
                decision="take_upstream",
                reason="source_omission",
            )
        ]
    return [
        _classify_path(identity, path, base_value, local_value, remote_value, resolutions)
        for path, base_value, local_value, remote_value in _path_triplets(section, identity, baseline, local, remote)
    ]


def issue_library_preview_token(
    plan: LibraryPlan,
    *,
    actor_id: int,
    authentication_revision: str,
    access_scope_fingerprint: str | None,
    signing_key: str | bytes,
    now: int | None = None,
) -> str:
    """Issue a 30-minute token bound to the entire plan and authorization."""

    binding = _binding_for_plan(plan)
    expected = PreviewTokenExpectation(
        actor_id=actor_id,
        authentication_revision=authentication_revision,
        access_scope_fingerprint=access_scope_fingerprint,
        command_kind="type-library.apply",
        target=None,
        normalized_input_digest=binding.input_digest,
        expected_resource_revision=plan.current_digest,
        expected_definition_revision=plan.plan_digest,
        expected_category_default_snapshot_revision=None,
        historical_state_digest=plan.snapshot_digest,
    )
    return issue_preview_token(expected, key=signing_key, now=now)


def verify_library_preview_token(
    token: str,
    plan: LibraryPlan,
    *,
    actor_id: int,
    authentication_revision: str,
    access_scope_fingerprint: str | None,
    signing_key: str | bytes,
    now: int | None = None,
) -> None:
    """Verify every plan, state, actor, scope, and expiry claim."""

    binding = _binding_for_plan(plan)
    expected = PreviewTokenExpectation(
        actor_id=actor_id,
        authentication_revision=authentication_revision,
        access_scope_fingerprint=access_scope_fingerprint,
        command_kind="type-library.apply",
        target=None,
        normalized_input_digest=binding.input_digest,
        expected_resource_revision=plan.current_digest,
        expected_definition_revision=plan.plan_digest,
        expected_category_default_snapshot_revision=None,
        historical_state_digest=plan.snapshot_digest,
    )
    verify_preview_token(token, expected=expected, key=signing_key, now=now)


def _binding_for_plan(plan: LibraryPlan) -> LibraryTokenBinding:
    return LibraryTokenBinding(
        namespace=plan.namespace,
        source_digest=plan.source_digest,
        snapshot_digest=plan.snapshot_digest,
        baseline_digest=plan.baseline_digest,
        current_digest=plan.current_digest,
        plan_digest=plan.plan_digest,
        resolutions=plan.resolutions,
    )


def _incoming_parts(
    incoming: ValidatedLibraryDocument,
) -> tuple[Mapping[str, Any], Mapping[str, Any], int, str | None]:
    root = incoming.normalized_document
    if incoming.kind == "itambox.type-library.release":
        source = root
        effective = root
        snapshot_digest = None
    else:
        source = root["upstream"]
        effective = {
            "requires": source.get("requires", []),
            "definitions": root["effective_definitions"],
        }
        snapshot_digest = incoming.semantic_digest
    return source, effective, _library_release(source), snapshot_digest


def _library_namespace(document: Mapping[str, Any]) -> str:
    library = document.get("library")
    if not isinstance(library, Mapping) or not isinstance(library.get("namespace"), str):
        raise LibraryPlanningError("INVALID_LIBRARY", "the normalized document has no library namespace")
    return library["namespace"]


def _library_release(document: Mapping[str, Any]) -> int:
    library = document.get("library")
    release = library.get("release") if isinstance(library, Mapping) else None
    if type(release) is not int or release < 1:
        raise LibraryPlanningError("INVALID_LIBRARY", "the normalized document has no positive release")
    return release


def _source_digest(document: Mapping[str, Any]) -> str:
    return _digest(document)


def _effective_digest(document: Mapping[str, Any]) -> str:
    payload = {
        "requires": deepcopy(document.get("requires", [])),
        "definitions": deepcopy(document.get("definitions", {})),
    }
    return _digest(payload)


def _digest(document: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonicalize_release_document(dict(document))).hexdigest()


def _entity_map(document: Mapping[str, Any] | None) -> dict[tuple[str, str], Mapping[str, Any]]:
    if document is None:
        return {}
    definitions = document.get("definitions", document)
    if not isinstance(definitions, Mapping):
        return {}
    entities: dict[tuple[str, str], Mapping[str, Any]] = {}
    for section, raw_items in definitions.items():
        if not isinstance(section, str) or not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            identity = _entity_identity(section, item)
            entities[(section, identity)] = item
    return entities


def _entity_identity(section: str, item: Mapping[str, Any]) -> str:
    identity = item.get("id")
    if isinstance(identity, str):
        return identity
    namespace = item.get("namespace")
    key = item.get("key") or item.get("slug")
    if isinstance(namespace, str) and isinstance(key, str):
        return f"{namespace}/{key}"
    if isinstance(key, str):
        return key
    return f"{section}:{_digest(item)}"


def _path_triplets(
    section: str,
    identity: str,
    baseline: Mapping[str, Any],
    local: Mapping[str, Any] | object,
    remote: Mapping[str, Any],
) -> list[tuple[tuple[str, ...], Any, Any, Any]]:
    prefix = ("definitions", section, identity)
    baseline_values = _flatten(baseline, prefix)
    local_values = _flatten(local, prefix) if local is not _MISSING else {}
    remote_values = _flatten(remote, prefix)
    paths = sorted(set(baseline_values) | set(local_values) | set(remote_values))
    return [
        (path, baseline_values.get(path, _MISSING), local_values.get(path, _MISSING), remote_values.get(path, _MISSING))
        for path in paths
    ]


def _flatten(value: Any, prefix: tuple[str, ...]) -> dict[tuple[str, ...], Any]:
    if isinstance(value, Mapping):
        if not value:
            return {prefix: {}}
        flattened: dict[tuple[str, ...], Any] = {}
        for key in sorted(value, key=str):
            flattened.update(_flatten(value[key], prefix + (str(key),)))
        return flattened
    if isinstance(value, list):
        return {prefix: deepcopy(value)}
    return {prefix: deepcopy(value)}


def _classify_path(
    identity: str,
    path: tuple[str, ...],
    baseline: Any,
    local: Any,
    remote: Any,
    resolutions: Mapping[str, str],
) -> LibraryPlanAction:
    if _equal(local, baseline) and _equal(remote, baseline):
        return _action("unchanged", identity, path, baseline, local, remote, decision="unchanged", reason="unchanged")
    if _equal(local, baseline) and not _equal(remote, baseline):
        return _action(
            "update", identity, path, baseline, local, remote, decision="take_upstream", reason="upstream_change"
        )
    if not _equal(local, baseline) and _equal(remote, baseline):
        return _action(
            "unchanged", identity, path, baseline, local, remote, decision="keep_local", reason="local_override"
        )
    if _equal(local, remote):
        return _action("unchanged", identity, path, baseline, local, remote, decision="unchanged", reason="converged")

    action_id = _action_id("conflict", identity, path, baseline, local, remote)
    requested = resolutions.get(action_id) or resolutions.get(".".join(path))
    decision = requested if requested in {"keep_local", "take_upstream", "abort"} else "abort"
    return LibraryPlanAction(
        action_id=action_id,
        action="conflict",
        identity=identity,
        path=path,
        baseline=deepcopy(None if baseline is _MISSING else baseline),
        local=deepcopy(None if local is _MISSING else local),
        incoming=deepcopy(None if remote is _MISSING else remote),
        decision=decision,
        reason="three_way_conflict",
    )


def _reference_action(baseline: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> LibraryPlanAction | None:
    before = [] if baseline is None else deepcopy(baseline.get("requires", []))
    after = deepcopy(incoming.get("requires", []))
    if before == after:
        return None
    return _action(
        "reference",
        "requires",
        ("requires",),
        before,
        before,
        after,
        decision="take_upstream",
        reason="dependency_change",
    )


def _action(
    action: PlanActionKind,
    identity: str,
    path: tuple[str, ...],
    baseline: Any,
    local: Any,
    incoming: Any,
    *,
    decision: str,
    reason: str,
) -> LibraryPlanAction:
    return LibraryPlanAction(
        action_id=_action_id(action, identity, path, baseline, local, incoming),
        action=action,
        identity=identity,
        path=path,
        baseline=deepcopy(None if baseline is _MISSING else baseline),
        local=deepcopy(None if local is _MISSING else local),
        incoming=deepcopy(None if incoming is _MISSING else incoming),
        decision=decision,
        reason=reason,
    )


def _action_id(action: str, identity: str, path: tuple[str, ...], baseline: Any, local: Any, incoming: Any) -> str:
    payload = {
        "version": 1,
        "action": action,
        "identity": identity,
        "path": list(path),
        "baseline": None if baseline is _MISSING else baseline,
        "local": None if local is _MISSING else local,
        "incoming": None if incoming is _MISSING else incoming,
    }
    return "sha256:" + hashlib.sha256(canonicalize_release_document(payload)).hexdigest()


def _plan_digest(
    *,
    namespace: str,
    incoming_release: int,
    source_digest: str,
    snapshot_digest: str | None,
    baseline_digest: str | None,
    current_digest: str | None,
    actions: list[LibraryPlanAction],
    resolutions: tuple[tuple[str, str], ...],
) -> str:
    payload = {
        "version": 1,
        "namespace": namespace,
        "incoming_release": incoming_release,
        "source_digest": source_digest,
        "snapshot_digest": snapshot_digest,
        "baseline_digest": baseline_digest,
        "current_digest": current_digest,
        "actions": [
            {
                "action_id": action.action_id,
                "action": action.action,
                "path": list(action.path),
                "decision": action.decision,
                "reason": action.reason,
            }
            for action in actions
        ],
        "resolutions": {key: value for key, value in resolutions},
    }
    return "sha256:" + hashlib.sha256(canonicalize_release_document(payload)).hexdigest()


def _equal(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return left == right


__all__ = [
    "LibraryPlan",
    "LibraryPlanAction",
    "LibraryPlanningError",
    "LibraryReconciliationState",
    "LibraryTokenBinding",
    "issue_library_preview_token",
    "plan_reconciliation",
    "verify_library_preview_token",
]
