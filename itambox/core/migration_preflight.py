"""Read-only recognition of the shipped migration transition state."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("migration_baseline_manifest.json")

_TRANSITION_REMEDIATION = (
    "Deploy the designated transition release, run the ordinary migration executor, "
    "and rerun migration_baseline_preflight."
)
_RESTORE_REMEDIATION = (
    "Stop the candidate, restore the verified predecessor, and compare schema, data, "
    "and protected-canary evidence before retrying."
)
_SHA_LENGTH = 40


@dataclass(frozen=True)
class PreflightResult:
    """Safe, machine-readable result of a migration recorder inspection."""

    state: str
    reason_code: str
    exit_code: int
    counts: Mapping[str, int]
    missing_ids: tuple[str, ...] = ()
    unexpected_ids: tuple[str, ...] = ()
    remediation: str = _RESTORE_REMEDIATION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "state": self.state,
            "reason_code": self.reason_code,
            "exit_code": self.exit_code,
            "counts": dict(self.counts),
            "missing_ids": list(self.missing_ids),
            "unexpected_ids": list(self.unexpected_ids),
            "remediation": self.remediation,
        }


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == _SHA_LENGTH and all(char in "0123456789abcdef" for char in value)


def _manifest_list(manifest: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = manifest.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"migration preflight manifest field {key} must be a list of strings")
    if value != sorted(set(value)):
        raise ValueError(f"migration preflight manifest field {key} must be sorted and unique")
    return tuple(value)


def _validate_manifest_relationships(manifest: Mapping[str, Any], values: Mapping[str, tuple[str, ...]]) -> None:
    if set(values["replacement_target_ids"]) != set(values["historical_ids"]):
        raise ValueError("migration preflight manifest replacement targets do not cover historical IDs")
    if not set(values["post_transition_leaf_ids"]).issubset(values["post_transition_ids"]):
        raise ValueError("migration preflight manifest post-transition leaves are not post-transition IDs")
    if manifest["layout"] == "transitional" and values["baseline_ids"] != values["replacement_ids"]:
        raise ValueError("migration preflight manifest transitional baseline IDs must equal replacement IDs")
    known_ids = set(values["replacement_ids"]) | set(values["historical_ids"]) | set(values["post_transition_ids"])
    if not set(values["baseline_ids"]).issubset(known_ids):
        raise ValueError("migration preflight manifest baseline IDs are not known migration IDs")


def _validate_manifest_predecessors(manifest: Mapping[str, Any]) -> None:
    if not _is_sha(manifest.get("transition_release_sha")):
        raise ValueError("migration preflight manifest transition_release_sha must be a lowercase 40-character Git SHA")
    predecessors = manifest.get("supported_predecessors")
    if not isinstance(predecessors, list) or not predecessors:
        raise ValueError("migration preflight manifest supported_predecessors must be a non-empty list")
    predecessor_names: set[str] = set()
    predecessor_revisions: set[str] = set()
    for predecessor in predecessors:
        if not isinstance(predecessor, dict):
            raise ValueError("migration preflight manifest predecessor entries must be objects")
        name = predecessor.get("name")
        revision = predecessor.get("revision")
        state = predecessor.get("state")
        if not isinstance(name, str) or not name.strip() or name in predecessor_names:
            raise ValueError("migration preflight manifest predecessor names must be unique and non-empty")
        if not _is_sha(revision):
            raise ValueError(
                "migration preflight manifest predecessor revisions must be lowercase 40-character Git SHAs"
            )
        if not isinstance(state, str) or not state.strip():
            raise ValueError("migration preflight manifest predecessor states must be non-empty strings")
        predecessor_names.add(name)
        predecessor_revisions.add(revision)
    if manifest["transition_release_sha"] not in predecessor_revisions:
        raise ValueError("migration preflight manifest transition release is not a named predecessor revision")


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("migration preflight manifest schema_version must be 1")
    if manifest.get("layout") not in {"transitional", "normalized"}:
        raise ValueError("migration preflight manifest layout is invalid")
    fields = (
        "first_party_apps",
        "historical_ids",
        "replacement_ids",
        "replacement_target_ids",
        "baseline_ids",
        "post_transition_ids",
        "post_transition_leaf_ids",
        "current_leaf_ids",
    )
    values = {key: _manifest_list(manifest, key) for key in fields}
    _validate_manifest_relationships(manifest, values)
    _validate_manifest_predecessors(manifest)


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the checked manifest without importing migrations."""

    manifest_path = MANIFEST_PATH if path is None else Path(path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to read migration preflight manifest") from exc
    if not isinstance(manifest, dict):
        raise ValueError("migration preflight manifest must be a JSON object")
    _validate_manifest_shape(manifest)
    return manifest


def _counts(manifest: Mapping[str, Any], observed: Mapping[str, set[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in ("historical", "replacement", "baseline", "post_transition", "post_transition_leaf"):
        expected = set(manifest[f"{name}_ids"])
        counts[f"{name}_expected"] = len(expected)
        counts[f"{name}_observed"] = len(observed[name] & expected)
    counts["first_party_observed"] = len(set().union(*observed.values()))
    return counts


def _result(
    *,
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    state: str,
    reason_code: str,
    exit_code: int,
    missing_ids: Iterable[str] = (),
    unexpected_ids: Iterable[str] = (),
    remediation: str = _RESTORE_REMEDIATION,
) -> PreflightResult:
    return PreflightResult(
        state=state,
        reason_code=reason_code,
        exit_code=exit_code,
        counts=_counts(manifest, observed),
        missing_ids=tuple(sorted(set(missing_ids))),
        unexpected_ids=tuple(sorted(set(unexpected_ids))),
        remediation=remediation,
    )


def _classify_normalized(
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    baseline: set[str],
    post_transition: set[str],
) -> PreflightResult:
    all_baseline = observed["baseline"] == baseline
    all_post_transition = observed["post_transition"] == post_transition
    all_post_leaves = observed["post_transition_leaf"] == set(manifest["post_transition_leaf_ids"])
    if all_baseline and all_post_transition and all_post_leaves:
        return _result(
            manifest=manifest,
            observed=observed,
            state="current-normalized-baseline",
            reason_code="NORMALIZED_BASELINE",
            exit_code=0,
        )
    if observed["post_transition"] and not all_baseline:
        return _result(
            manifest=manifest,
            observed=observed,
            state="mixed-or-unknown-first-party-state",
            reason_code="POST_TRANSITION_WITHOUT_BASELINE",
            exit_code=1,
        )
    if not all_baseline:
        return _result(
            manifest=manifest,
            observed=observed,
            state="partial-normalized-baseline",
            reason_code="NORMALIZED_BASELINE_INCOMPLETE",
            exit_code=1,
            missing_ids=baseline - observed["baseline"],
        )
    return _result(
        manifest=manifest,
        observed=observed,
        state="partial-post-transition-state",
        reason_code="POST_TRANSITION_INCOMPLETE",
        exit_code=1,
        missing_ids=post_transition - observed["post_transition"],
    )


def _classify_complete_transitional(
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    post_transition: set[str],
) -> PreflightResult:
    if observed["post_transition"] == post_transition and observed["post_transition_leaf"] == set(
        manifest["post_transition_leaf_ids"]
    ):
        return _result(
            manifest=manifest,
            observed=observed,
            state="complete-replacement-recognition",
            reason_code="REPLACEMENT_RECOGNIZED",
            exit_code=0,
            remediation="No migration action is required; continue only after independent release checks pass.",
        )
    return _result(
        manifest=manifest,
        observed=observed,
        state="partial-post-transition-state",
        reason_code="POST_TRANSITION_INCOMPLETE",
        exit_code=1,
        missing_ids=post_transition - observed["post_transition"],
    )


def _classify_all_replacement(
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    historical: set[str],
) -> PreflightResult:
    if not observed["historical"]:
        return _result(
            manifest=manifest,
            observed=observed,
            state="normalized-baseline-not-current",
            reason_code="NORMALIZED_LAYOUT_NOT_CURRENT",
            exit_code=1,
        )
    return _result(
        manifest=manifest,
        observed=observed,
        state="mixed-or-unknown-first-party-state",
        reason_code="REPLACEMENT_WITH_INCOMPLETE_HISTORY",
        exit_code=1,
        missing_ids=historical - observed["historical"],
    )


def _classify_historical_only(
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    historical: set[str],
    replacement: set[str],
    post_transition: set[str],
) -> PreflightResult:
    if observed["post_transition"]:
        return _result(
            manifest=manifest,
            observed=observed,
            state="mixed-or-unknown-first-party-state",
            reason_code="POST_TRANSITION_WITHOUT_REPLACEMENT",
            exit_code=1,
        )
    return _result(
        manifest=manifest,
        observed=observed,
        state="complete-old-history-no-replacement",
        reason_code="TRANSITION_RELEASE_REQUIRED",
        exit_code=1,
        missing_ids=replacement | post_transition,
        remediation=_TRANSITION_REMEDIATION,
    )


def _classify_transitional(
    manifest: Mapping[str, Any],
    observed: Mapping[str, set[str]],
    historical: set[str],
    replacement: set[str],
    post_transition: set[str],
) -> PreflightResult:
    all_historical = observed["historical"] == historical
    all_replacement = observed["replacement"] == replacement
    if all_historical and all_replacement:
        return _classify_complete_transitional(manifest, observed, post_transition)
    if all_replacement:
        return _classify_all_replacement(manifest, observed, historical)
    if observed["replacement"]:
        return _result(
            manifest=manifest,
            observed=observed,
            state="partial-replacement-set",
            reason_code="PARTIAL_REPLACEMENT_SET",
            exit_code=1,
            missing_ids=replacement - observed["replacement"],
        )
    if all_historical:
        return _classify_historical_only(manifest, observed, historical, replacement, post_transition)
    if observed["historical"]:
        if observed["post_transition"]:
            return _result(
                manifest=manifest,
                observed=observed,
                state="mixed-or-unknown-first-party-state",
                reason_code="POST_TRANSITION_WITH_INCOMPLETE_BASELINE",
                exit_code=1,
            )
        return _result(
            manifest=manifest,
            observed=observed,
            state="partial-old-history",
            reason_code="PARTIAL_OLD_HISTORY",
            exit_code=1,
            missing_ids=historical - observed["historical"],
        )
    return _result(
        manifest=manifest,
        observed=observed,
        state="mixed-or-unknown-first-party-state",
        reason_code="BASELINE_STATE_UNRECOGNIZED",
        exit_code=1,
    )


def classify_applied_migrations(applied_ids: Iterable[str], manifest: Mapping[str, Any]) -> PreflightResult:
    """Classify first-party recorder IDs without changing database state."""

    _validate_manifest_shape(manifest)
    applied = {item for item in applied_ids if isinstance(item, str)}
    historical = set(manifest["historical_ids"])
    replacement = set(manifest["replacement_ids"])
    baseline = set(manifest["baseline_ids"])
    post_transition = set(manifest["post_transition_ids"])
    post_leaves = set(manifest["post_transition_leaf_ids"])
    first_party_apps = set(manifest["first_party_apps"])
    known = historical | replacement | baseline | post_transition
    first_party_applied = {item for item in applied if item.split(".", 1)[0] in first_party_apps}
    unknown = first_party_applied - known
    observed = {
        "historical": first_party_applied & historical,
        "replacement": first_party_applied & replacement,
        "baseline": first_party_applied & baseline,
        "post_transition": first_party_applied & post_transition,
        "post_transition_leaf": first_party_applied & post_leaves,
    }

    if unknown:
        return _result(
            manifest=manifest,
            observed=observed,
            state="mixed-or-unknown-first-party-state",
            reason_code="UNKNOWN_FIRST_PARTY_MIGRATION",
            exit_code=1,
            unexpected_ids=unknown,
        )
    if not first_party_applied:
        return _result(
            manifest=manifest,
            observed=observed,
            state="empty-or-unmigrated",
            reason_code="NO_FIRST_PARTY_MIGRATIONS",
            exit_code=1,
        )
    if manifest["layout"] == "normalized":
        return _classify_normalized(manifest, observed, baseline, post_transition)
    return _classify_transitional(manifest, observed, historical, replacement, post_transition)


def manifest_invalid_result() -> PreflightResult:
    """Return a safe, structured failure for an unusable checked manifest."""

    return PreflightResult(
        state="migration-preflight-manifest-invalid",
        reason_code="MIGRATION_PREFLIGHT_MANIFEST_INVALID",
        exit_code=1,
        counts={},
        remediation="Restore the checked manifest from the exact reviewed source before retrying; do not run cleanup.",
    )


def recorder_unavailable_result(*, missing_table: bool = False) -> PreflightResult:
    """Return a safe failure for a missing or unreadable recorder table."""

    if missing_table:
        return PreflightResult(
            state="migration-recorder-missing",
            reason_code="MIGRATION_RECORDER_TABLE_MISSING",
            exit_code=1,
            counts={},
            remediation=_TRANSITION_REMEDIATION,
        )
    return PreflightResult(
        state="migration-recorder-unavailable",
        reason_code="MIGRATION_RECORDER_UNAVAILABLE",
        exit_code=1,
        counts={},
        remediation=_RESTORE_REMEDIATION,
    )


def format_table(result: PreflightResult) -> str:
    """Render a human-readable result without database or secret values."""

    lines = [f"state: {result.state}", f"reason_code: {result.reason_code}", f"exit_code: {result.exit_code}"]
    for name, value in result.counts.items():
        lines.append(f"{name}: {value}")
    if result.missing_ids:
        lines.append(f"missing_ids: {', '.join(result.missing_ids)}")
    if result.unexpected_ids:
        lines.append(f"unexpected_ids: {', '.join(result.unexpected_ids)}")
    lines.append(f"remediation: {result.remediation}")
    return "\n".join(lines)
