"""DB-free T18/T20 contract tests for reconciliation planning."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from assets.services.specifications.preview_tokens import PreviewTokenError
from assets.services.type_library.application import (
    LibraryApplyError,
    LibraryApplyRequest,
    prepare_library_apply,
)
from assets.services.type_library.exporting import (
    LibraryExportError,
    export_effective_snapshot,
    export_fork,
    export_original_release,
)
from assets.services.type_library.planning import (
    LibraryPlanningError,
    LibraryReconciliationState,
    issue_library_preview_token,
    plan_reconciliation,
    verify_library_preview_token,
)
from assets.services.type_library_validation import validate_library_document
from assets.services.type_library_validation.validation import ValidatedLibraryDocument
from assets.tests.test_t17_type_library_validation import _release_document


def _validated(document: dict[str, object]) -> ValidatedLibraryDocument:
    return validate_library_document(json.dumps(document, ensure_ascii=False))


def _state(document: dict[str, object], *, local: dict[str, object] | None = None) -> LibraryReconciliationState:
    normalized_document = _validated(document).normalized_document
    normalized_local = _validated(local).normalized_document if local is not None else normalized_document
    return LibraryReconciliationState(
        namespace=normalized_document["library"]["namespace"],  # type: ignore[index]
        accepted_release=normalized_document["library"]["release"],  # type: ignore[index]
        baseline_document=deepcopy(normalized_document),
        effective_document=deepcopy(normalized_local),
    )


def test_identical_release_is_a_deterministic_noop():
    document = _release_document()

    plan = plan_reconciliation(_state(document), _validated(deepcopy(document)))

    assert plan.can_apply is True
    assert plan.conflicts == ()
    assert plan.actions
    assert all(action.decision == "unchanged" for action in plan.actions)


def test_upstream_change_when_local_matches_baseline_is_an_update():
    baseline = _release_document()
    incoming = deepcopy(baseline)
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["fields"][0]["label"] = "Changed state"  # type: ignore[index]

    plan = plan_reconciliation(_state(baseline), _validated(incoming))

    updates = [action for action in plan.actions if action.decision == "take_upstream"]
    assert updates
    assert any(action.path[-1] == "label" for action in updates)
    assert plan.conflicts == ()


@pytest.mark.parametrize("case", ["local_override", "converged", "three_way_conflict"])
def test_reconciliation_cases_are_exposed(case: str):
    baseline = _release_document()
    local = deepcopy(baseline)
    incoming = deepcopy(baseline)
    local_field = local["definitions"]["fields"][0]  # type: ignore[index]
    incoming_field = incoming["definitions"]["fields"][0]  # type: ignore[index]
    if case == "local_override":
        local_field["label"] = "Local label"
        incoming["library"]["release"] = 2  # type: ignore[index]
    elif case == "converged":
        local_field["label"] = "Converged label"
        incoming_field["label"] = "Converged label"
        incoming["library"]["release"] = 2  # type: ignore[index]
    else:
        local_field["label"] = "Local label"
        incoming_field["label"] = "Incoming label"
        incoming["library"]["release"] = 2  # type: ignore[index]

    plan = plan_reconciliation(_state(baseline, local=local), _validated(incoming))

    if case == "three_way_conflict":
        assert plan.can_apply is False
        assert plan.conflicts
    else:
        assert plan.can_apply is True
        assert plan.conflicts == ()


def test_ordered_list_is_one_atomic_conflict_path():
    baseline = _release_document()
    baseline["definitions"]["choice_sets"][0]["choices"].append(  # type: ignore[index]
        {"key": "maybe", "label": "Maybe", "lifecycle": "active"}
    )
    local = deepcopy(baseline)
    incoming = deepcopy(baseline)
    local["definitions"]["choice_sets"][0]["choices"] = [  # type: ignore[index]
        local["definitions"]["choice_sets"][0]["choices"][2],  # type: ignore[index]
        local["definitions"]["choice_sets"][0]["choices"][0],  # type: ignore[index]
        local["definitions"]["choice_sets"][0]["choices"][1],  # type: ignore[index]
    ]
    incoming["definitions"]["choice_sets"][0]["choices"] = [  # type: ignore[index]
        incoming["definitions"]["choice_sets"][0]["choices"][1],  # type: ignore[index]
        incoming["definitions"]["choice_sets"][0]["choices"][2],  # type: ignore[index]
        incoming["definitions"]["choice_sets"][0]["choices"][0],  # type: ignore[index]
    ]
    incoming["library"]["release"] = 2  # type: ignore[index]

    plan = plan_reconciliation(_state(baseline, local=local), _validated(incoming))

    conflicts = [action for action in plan.conflicts if action.path[-1] == "choices"]
    assert len(conflicts) == 1
    assert plan.can_apply is False


def test_source_omission_proposes_deprecation_but_not_catalogue_deletion():
    baseline = _release_document()
    incoming = deepcopy(baseline)
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["asset_types"] = []  # type: ignore[index]
    incoming["definitions"]["categories"] = []  # type: ignore[index]
    incoming["definitions"]["manufacturers"] = []  # type: ignore[index]

    plan = plan_reconciliation(_state(baseline), _validated(incoming))

    assert any(action.action == "deprecate" and action.identity == "acme/device-a" for action in plan.actions)
    assert not any(action.action == "deprecate" and action.identity == "catalog/devices" for action in plan.actions)
    assert not any(action.action == "deprecate" and action.identity == "catalog/acme" for action in plan.actions)


def test_older_release_and_same_sequence_equivocation_are_rejected():
    baseline = _release_document()
    accepted_newer = deepcopy(baseline)
    accepted_newer["library"]["release"] = 2  # type: ignore[index]
    with pytest.raises(LibraryPlanningError) as older_error:
        plan_reconciliation(_state(accepted_newer), _validated(baseline))
    assert older_error.value.code == "OLDER_RELEASE"

    equivocation = deepcopy(baseline)
    equivocation["definitions"]["fields"][0]["label"] = "Equivocated"  # type: ignore[index]
    with pytest.raises(LibraryPlanningError) as caught:
        plan_reconciliation(_state(baseline), _validated(equivocation))
    assert caught.value.code == "EQUIVOCATION"

    newer = deepcopy(baseline)
    newer["library"]["release"] = 2  # type: ignore[index]
    plan_reconciliation(_state(baseline), _validated(newer))


def test_structural_field_change_is_rejected_before_three_way_planning():
    baseline = _release_document()
    incoming = deepcopy(baseline)
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["fields"][0]["targets"] = ["asset_type", "asset"]  # type: ignore[index]

    with pytest.raises(LibraryPlanningError) as caught:
        plan_reconciliation(_state(baseline), _validated(incoming))
    assert caught.value.code == "UNSUPPORTED_STRUCTURE"


def test_preview_token_binds_source_state_resolutions_actor_scope_and_expiry():
    baseline = _release_document()
    incoming = deepcopy(baseline)
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["fields"][0]["label"] = "Incoming"  # type: ignore[index]
    local = deepcopy(baseline)
    local["definitions"]["fields"][0]["label"] = "Local"  # type: ignore[index]
    plan = plan_reconciliation(_state(baseline, local=local), _validated(incoming))
    token = issue_library_preview_token(
        plan,
        actor_id=7,
        authentication_revision="auth-1",
        access_scope_fingerprint="scope-1",
        signing_key="test-only-key",
        now=100,
    )

    verify_library_preview_token(
        token,
        plan,
        actor_id=7,
        authentication_revision="auth-1",
        access_scope_fingerprint="scope-1",
        signing_key="test-only-key",
        now=100 + 1_799,
    )
    with pytest.raises(PreviewTokenError):
        verify_library_preview_token(
            token,
            plan,
            actor_id=8,
            authentication_revision="auth-1",
            access_scope_fingerprint="scope-1",
            signing_key="test-only-key",
            now=100,
        )
    with pytest.raises(PreviewTokenError):
        verify_library_preview_token(
            token,
            plan,
            actor_id=7,
            authentication_revision="auth-1",
            access_scope_fingerprint="scope-1",
            signing_key="test-only-key",
            now=1_900,
        )


def test_apply_recomputes_the_bound_plan_before_any_write():
    baseline = _release_document()
    local = deepcopy(baseline)
    incoming = deepcopy(baseline)
    local["definitions"]["fields"][0]["label"] = "Local label"  # type: ignore[index]
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["fields"][0]["label"] = "Upstream label"  # type: ignore[index]
    state = _state(baseline, local=local)
    incoming_validated = _validated(incoming)
    unresolved = plan_reconciliation(state, incoming_validated)
    resolutions = {action.action_id: "take_upstream" for action in unresolved.conflicts}
    plan = plan_reconciliation(state, incoming_validated, resolutions=resolutions)
    token = issue_library_preview_token(
        plan,
        actor_id=7,
        authentication_revision="auth-r1",
        access_scope_fingerprint="scope-a",
        signing_key="test-only-key",
    )
    request = LibraryApplyRequest(
        plan=plan,
        token=token,
        actor_id=7,
        authentication_revision="auth-r1",
        access_scope_fingerprint="scope-a",
        signing_key="test-only-key",
    )

    prepared = prepare_library_apply(
        request,
        incoming_validated,
        state,
        authorize=lambda: True,
    )

    assert prepared.plan_digest == plan.plan_digest
    assert prepared.can_apply is True


def test_apply_rejects_state_drift_and_current_authorization_failure():
    baseline = _release_document()
    incoming = deepcopy(baseline)
    incoming["library"]["release"] = 2  # type: ignore[index]
    incoming["definitions"]["fields"][0]["label"] = "Upstream label"  # type: ignore[index]
    state = _state(baseline)
    incoming_validated = _validated(incoming)
    plan = plan_reconciliation(state, incoming_validated)
    token = issue_library_preview_token(
        plan,
        actor_id=7,
        authentication_revision="auth-r1",
        access_scope_fingerprint="scope-a",
        signing_key="test-only-key",
    )
    request = LibraryApplyRequest(
        plan=plan,
        token=token,
        actor_id=7,
        authentication_revision="auth-r1",
        access_scope_fingerprint="scope-a",
        signing_key="test-only-key",
    )
    drifted = _state(baseline, local=deepcopy(incoming))

    with pytest.raises(LibraryApplyError, match="STALE_PLAN"):
        prepare_library_apply(request, incoming_validated, drifted, authorize=lambda: True)
    with pytest.raises(LibraryApplyError, match="OBJECT_UNAVAILABLE"):
        prepare_library_apply(request, incoming_validated, state, authorize=lambda: False)


def _retained_fixture(name: str) -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "type_library" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_original_export_preserves_the_retained_source_digest():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))

    artifact = export_original_release(release)

    assert artifact.mode == "original_release"
    assert artifact.identity_changed is False
    assert artifact.semantic_digest == release.semantic_digest
    assert artifact.document == release.normalized_document


def test_effective_snapshot_round_trips_retained_upstream_and_local_24gib_edit():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))
    source_snapshot = _validated(_retained_fixture("example-laptop-snapshot-v1.json"))
    effective = source_snapshot.normalized_document["effective_definitions"]

    artifact = export_effective_snapshot(release, effective)
    repeated = export_effective_snapshot(release, artifact.document["effective_definitions"])
    restored = _validated(json.loads(artifact.canonical_bytes.decode("utf-8")))

    assert artifact.mode == "effective_snapshot"
    assert artifact.source_digest == release.semantic_digest
    assert artifact.semantic_digest == source_snapshot.semantic_digest
    assert repeated.semantic_digest == artifact.semantic_digest
    assert restored.semantic_digest == artifact.semantic_digest
    assert (
        artifact.document["effective_definitions"]["asset_types"][0]["specifications"]["example__memory_capacity"]
        == "24.000"
    )


def test_snapshot_structural_change_is_not_a_local_override():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))
    effective = deepcopy(release.normalized_document["definitions"])
    effective["fields"][0]["targets"] = ["asset_type"]  # type: ignore[index]

    with pytest.raises(LibraryExportError, match="SNAPSHOT_STRUCTURAL_MISMATCH"):
        export_effective_snapshot(release, effective)


def test_retained_history_requires_explicit_acknowledgement():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))
    effective = deepcopy(release.normalized_document["definitions"])
    effective["choice_sets"][0]["choices"].append(  # type: ignore[index]
        {"key": "legacy_ddr", "label": "Legacy DDR", "lifecycle": "deprecated"}
    )

    with pytest.raises(LibraryExportError, match="RETAINED_HISTORY_ACK_REQUIRED"):
        export_effective_snapshot(release, effective)
    acknowledged = export_effective_snapshot(
        release,
        effective,
        acknowledge_retained_history=True,
    )
    assert acknowledged.document["effective_definitions"]["choice_sets"][0]["choices"][-1]["key"] == "legacy_ddr"


def test_unknown_historical_field_blocks_export_with_a_path_diagnostic():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))
    effective = deepcopy(release.normalized_document["definitions"])
    effective["asset_types"][0]["historical_specifications"] = {  # type: ignore[index]
        "example__does_not_exist": {"value": "retired"}
    }

    with pytest.raises(LibraryExportError) as caught:
        export_effective_snapshot(release, effective)
    assert caught.value.issues
    assert any("historical_specifications" in str(issue.path) for issue in caught.value.issues)


def test_fork_requires_new_namespace_and_rewrites_owned_identities():
    release = _validated(_retained_fixture("example-laptop-library-v1.json"))

    artifact = export_fork(release, new_namespace="local-laptop")
    forked = _validated(json.loads(artifact.canonical_bytes.decode("utf-8")))

    assert artifact.mode == "fork"
    assert artifact.identity_changed is True
    assert artifact.document["library"]["namespace"] == "local-laptop"
    assert artifact.document["requires"] == release.normalized_document["requires"]
    assert artifact.document["definitions"]["fields"][0]["namespace"] == "local-laptop"
    assert artifact.document["definitions"]["asset_types"][0]["id"].startswith("local-laptop/")
    assert forked.semantic_digest == artifact.semantic_digest
