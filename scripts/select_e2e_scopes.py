#!/usr/bin/env python
"""Select fail-closed Playwright scopes from an exact Git change set.

The module intentionally depends only on the Python standard library so the
detector can run before application dependencies are installed.  The JSON
selection is the authoritative artifact; workflow outputs are only projections
of a selection which has already passed :func:`validate_selection`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = 1
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
RULE_ID_RE = re.compile(r"[a-z0-9][a-z0-9:-]*\Z")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SPEC_SUFFIXES = (".spec.ts", ".test.ts")
CHANGE_STATUSES = frozenset({"A", "M", "D", "R", "C"})
SELECTIVE_EVENTS = frozenset({"pull_request"})
AUTHORITATIVE_FULL_EVENTS = frozenset({"push", "schedule", "workflow_dispatch", "workflow_call", "release"})
KNOWN_EVENTS = SELECTIVE_EVENTS | AUTHORITATIVE_FULL_EVENTS

REQUIRED_APP_SCOPES = frozenset(
    {
        "app:organization",
        "app:assets",
        "app:inventory",
        "app:software",
        "app:licenses",
        "app:subscriptions",
        "app:procurement",
        "app:compliance",
        "app:extras",
        "app:users",
        "app:core",
        "app:itambox",
    }
)
REQUIRED_CONTRACT_SCOPES = frozenset(
    {
        "contract:generic-object",
        "contract:tenant-isolation",
        "contract:auth-rbac",
        "contract:soft-delete",
        "contract:asset-custody",
        "contract:cross-app",
        "contract:jobs",
    }
)
REQUIRED_SHARED_SCOPES = frozenset({"smoke", "legacy-smoke", "layout", "a11y"})
REQUIRED_CATALOG_SCOPES = REQUIRED_APP_SCOPES | REQUIRED_CONTRACT_SCOPES | REQUIRED_SHARED_SCOPES | {"all"}

MAP_KEYS = frozenset(
    {
        "schema",
        "spec_root",
        "full_spec_path",
        "always_run_scopes",
        "full_scopes",
        "scopes",
        "rules",
        "known_production_roots",
        "rollback",
    }
)
SCOPE_KEYS = frozenset({"path", "kind", "owner"})
RULE_KEYS = frozenset({"id", "decision", "patterns", "scopes"})
ROLLBACK_KEYS = frozenset({"force_full_pr_selection", "switch_path"})
SELECTION_KEYS = frozenset(
    {
        "schema",
        "mode",
        "event_name",
        "base_sha",
        "head_sha",
        "merge_base_sha",
        "changed_path_digest",
        "scopes",
        "spec_paths",
        "reasons",
    }
)


class ScopeMapError(ValueError):
    """The repository-owned scope map is malformed or has drifted."""


class SelectionError(ValueError):
    """A change set or selection artifact cannot be trusted."""


@dataclass(frozen=True, order=True)
class ChangeRecord:
    """One normalized record from ``git diff --name-status -z``."""

    status: str
    old_path: str | None = None
    new_path: str | None = None

    def identities(self) -> tuple[str, ...]:
        values = [value for value in (self.old_path, self.new_path) if value is not None]
        return tuple(sorted(set(values)))

    def as_dict(self) -> dict[str, str | None]:
        return {"new_path": self.new_path, "old_path": self.old_path, "status": self.status}


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used by policy artifacts."""

    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json_file(path: str | Path, *, label: str = "JSON artifact") -> Any:
    """Load strict UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""

    source = Path(path)
    try:
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SelectionError(f"cannot read {label} at {source}: {exc}") from exc
    if not raw:
        raise SelectionError(f"{label} at {source} is empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SelectionError(f"{label} at {source} is not UTF-8") from exc
    if text.startswith("\ufeff"):
        raise SelectionError(f"{label} at {source} must not contain a UTF-8 BOM")
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SelectionError(f"cannot parse {label} at {source} as strict JSON: {exc}") from exc


def _expect_exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise ValueError(f"{label} has invalid keys: {'; '.join(details)}")


def _expect_list(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _expect_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonblank string")
    if CONTROL_RE.search(value):
        raise ValueError(f"{label} contains a control character")
    return value


def _validate_relative_path(value: Any, label: str, *, allow_glob: bool = False) -> str:
    path = _expect_string(value, label)
    if "\\" in path:
        raise ValueError(f"{label} must use POSIX separators")
    if path.startswith("/") or re.match(r"[A-Za-z]:", path):
        raise ValueError(f"{label} must be repository-relative")
    if path.endswith("/") or "//" in path:
        raise ValueError(f"{label} is not normalized")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must not contain empty, dot, or parent segments")
    if not allow_glob and any(character in path for character in "*?["):
        raise ValueError(f"{label} must not contain glob metacharacters")
    return path


def _validate_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase 40-character Git SHA")
    return value


def _validate_digest(value: Any, label: str = "changed_path_digest") -> str:
    if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
        raise ValueError(f"{label} must be sha256 followed by a lowercase 64-character digest")
    return value


def _validate_sorted_unique_strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    rows = _expect_list(value, label, nonempty=nonempty)
    if any(not isinstance(row, str) or not row for row in rows):
        raise ValueError(f"{label} must contain nonblank strings")
    if rows != sorted(rows):
        raise ValueError(f"{label} must use canonical lexical sorting")
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} must not contain duplicates")
    for row in rows:
        if CONTROL_RE.search(row):
            raise ValueError(f"{label} contains a control character")
    return rows


def _validate_unique_strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    rows = _expect_list(value, label, nonempty=nonempty)
    if any(not isinstance(row, str) or not row or CONTROL_RE.search(row) for row in rows):
        raise ValueError(f"{label} must contain control-free nonblank strings")
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} must not contain duplicates")
    return rows


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile the small, path-aware glob grammar used by the scope map."""

    pieces = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                while index < len(pattern) and pattern[index] == "*":
                    index += 1
                if index < len(pattern) and pattern[index] == "/":
                    pieces.append("(?:[^/]+/)*")
                    index += 1
                else:
                    pieces.append(".*")
                continue
            pieces.append("[^/]*")
        elif character == "?":
            pieces.append("[^/]")
        elif character == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                raise ValueError(f"glob pattern {pattern!r} has an unterminated character class")
            content = pattern[index + 1 : end]
            if not content or "/" in content or "\\" in content:
                raise ValueError(f"glob pattern {pattern!r} has an invalid character class")
            if content.startswith("!"):
                content = "^" + content[1:]
            elif content.startswith("^"):
                content = "\\" + content
            pieces.append("[" + content + "]")
            index = end
        else:
            pieces.append(re.escape(character))
        index += 1
    pieces.append("$")
    return re.compile("".join(pieces))


def _matches(path: str, pattern: str) -> bool:
    return _glob_regex(pattern).fullmatch(path) is not None


def _literal_prefix(pattern: str) -> tuple[str, ...]:
    result = []
    for segment in pattern.split("/"):
        if any(character in segment for character in "*?["):
            break
        result.append(segment)
    return tuple(result)


def _patterns_may_overlap(first: str, second: str) -> bool:
    """Conservatively identify possible overlap for safe-ignore validation."""

    first_prefix = _literal_prefix(first)
    second_prefix = _literal_prefix(second)
    common = min(len(first_prefix), len(second_prefix))
    if first_prefix[:common] != second_prefix[:common]:
        return False
    if _matches(first, second) or _matches(second, first):
        return True
    # A shared literal prefix followed by a wildcard is deliberately treated as
    # overlap.  Safe-ignore declarations must be obviously disjoint from code.
    return True


def _pattern_covers(container: str, target: str) -> bool:
    if container == target:
        return True
    if not any(character in target for character in "*?[") and _matches(target, container):
        return True
    if container.endswith("/**"):
        prefix = container[:-3].rstrip("/")
        target_prefix = "/".join(_literal_prefix(target))
        return target_prefix == prefix or target_prefix.startswith(prefix + "/")
    return False


def _contained_path(root: Path, relative: str, label: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside {resolved_root}") from exc
    return candidate


def _discover_specs(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.name.endswith(SPEC_SUFFIXES) else []
    if not path.is_dir():
        return []
    return sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file() and candidate.name.endswith(SPEC_SUFFIXES)
    )


def validate_scope_map(
    document: Any,
    repo_root: str | Path,
    require_catalog_scopes: bool = True,
    *,
    require_spec_evidence: bool = True,
) -> dict[str, Any]:
    """Validate the complete scope-map policy and its on-disk spec evidence.

    ``require_catalog_scopes=False`` is intended for focused unit maps.  The
    independent ``require_spec_evidence`` switch exists for map review during a
    coordinated directory migration; execution must always leave it enabled.
    """

    try:
        if not isinstance(document, dict):
            raise ValueError("scope map must be a JSON object")
        _expect_exact_keys(document, MAP_KEYS, "scope map")
        if type(document["schema"]) is not int or document["schema"] != SCHEMA:
            raise ValueError(f"scope map schema must be {SCHEMA}")

        spec_root_value = _validate_relative_path(document["spec_root"], "scope map spec_root")
        full_spec_path = _validate_relative_path(document["full_spec_path"], "scope map full_spec_path")
        spec_root = _contained_path(Path(repo_root), spec_root_value, "scope map spec_root")
        full_path = _contained_path(spec_root, full_spec_path, "scope map full_spec_path")

        scopes = document["scopes"]
        if not isinstance(scopes, dict) or not scopes:
            raise ValueError("scope map scopes must be a nonempty object")
        scope_paths: dict[str, str] = {}
        for name, entry in scopes.items():
            _expect_string(name, "scope name")
            if not isinstance(entry, dict):
                raise ValueError(f"scope {name!r} must be an object")
            required_scope_keys = {"path", "kind"}
            actual_scope_keys = set(entry)
            if not required_scope_keys.issubset(actual_scope_keys) or not actual_scope_keys.issubset(SCOPE_KEYS):
                raise ValueError(f"scope {name!r} must contain path/kind and only supported keys")
            scope_path = _validate_relative_path(entry["path"], f"scope {name!r} path")
            kind = _expect_string(entry["kind"], f"scope {name!r} kind")
            if kind not in {"app", "contract", "qualification", "special", "workflow"}:
                raise ValueError(f"scope {name!r} has unsupported kind {kind!r}")
            if "owner" in entry:
                _expect_string(entry["owner"], f"scope {name!r} owner")
            target = _contained_path(spec_root, scope_path, f"scope {name!r} path")
            try:
                target.relative_to(full_path)
            except ValueError as exc:
                raise ValueError(f"scope {name!r} path must be beneath {full_spec_path!r}") from exc
            if require_spec_evidence and not _discover_specs(target):
                raise ValueError(f"scope {name!r} path {scope_path!r} has no discovered .spec.ts/.test.ts evidence")
            scope_paths[name] = scope_path

        always = _validate_unique_strings(document["always_run_scopes"], "always_run_scopes", nonempty=True)
        full_scopes = _validate_unique_strings(document["full_scopes"], "full_scopes", nonempty=True)
        for name in always + full_scopes:
            if name not in scopes and not (not require_catalog_scopes and name == "all"):
                raise ValueError(f"declared scope {name!r} is not defined")

        rules = _expect_list(document["rules"], "scope map rules", nonempty=True)
        rule_ids: set[str] = set()
        non_safe_patterns: list[tuple[str, str]] = []
        safe_patterns: list[tuple[str, str]] = []
        selected_scope_owners: set[str] = set()
        for index, rule in enumerate(rules):
            label = f"scope map rule {index}"
            if not isinstance(rule, dict):
                raise ValueError(f"{label} must be an object")
            required = {"id", "decision", "patterns"}
            if not required.issubset(rule) or not set(rule).issubset(RULE_KEYS):
                raise ValueError(f"{label} must contain id/decision/patterns and only supported keys")
            rule_id = _expect_string(rule["id"], f"{label} id")
            if not RULE_ID_RE.fullmatch(rule_id):
                raise ValueError(f"{label} id is not a stable policy token")
            if rule_id in rule_ids:
                raise ValueError(f"duplicate rule id {rule_id!r}")
            rule_ids.add(rule_id)
            decision = rule["decision"]
            if decision not in {"full", "selected", "safe_ignore"}:
                raise ValueError(f"rule {rule_id!r} has unsupported decision {decision!r}")
            patterns = _validate_unique_strings(rule["patterns"], f"rule {rule_id!r} patterns", nonempty=True)
            for pattern in patterns:
                _validate_relative_path(pattern, f"rule {rule_id!r} pattern", allow_glob=True)
                _glob_regex(pattern)
                (safe_patterns if decision == "safe_ignore" else non_safe_patterns).append((rule_id, pattern))
            selected = rule.get("scopes")
            if decision == "selected":
                selected = _validate_unique_strings(selected, f"rule {rule_id!r} scopes", nonempty=True)
                unknown = sorted(set(selected) - set(scopes))
                if unknown:
                    raise ValueError(f"rule {rule_id!r} names unknown scopes {unknown}")
                forbidden = sorted(set(selected) & set(full_scopes))
                if forbidden:
                    raise ValueError(f"selected rule {rule_id!r} names full-only scopes {forbidden}")
                selected_scope_owners.update(selected)
            elif selected is not None:
                raise ValueError(f"rule {rule_id!r} may not declare scopes for decision {decision!r}")

        known_roots = _validate_unique_strings(
            document["known_production_roots"], "known_production_roots", nonempty=True
        )
        for root_pattern in known_roots:
            _validate_relative_path(root_pattern, "known production root", allow_glob=True)
            _glob_regex(root_pattern)
            # A known root is itself a fail-closed classification boundary:
            # unmatched descendants escalate through ``unknown-production``.
            # Individual app/shared rules intentionally need not cover the
            # entire root (the unit map uses ``src/**`` in exactly this way).

        for safe_id, safe_pattern in safe_patterns:
            for other_id, other_pattern in non_safe_patterns:
                if _patterns_may_overlap(safe_pattern, other_pattern):
                    raise ValueError(
                        f"safe-ignore rule {safe_id!r} pattern {safe_pattern!r} may overlap "
                        f"non-safe rule {other_id!r} pattern {other_pattern!r}"
                    )
            for root_pattern in known_roots:
                if _patterns_may_overlap(safe_pattern, root_pattern):
                    raise ValueError(
                        f"safe-ignore rule {safe_id!r} pattern {safe_pattern!r} may overlap "
                        f"known production root {root_pattern!r}"
                    )

        rollback = document["rollback"]
        if not isinstance(rollback, dict):
            raise ValueError("rollback must be an object")
        _expect_exact_keys(rollback, ROLLBACK_KEYS, "rollback")
        if type(rollback["force_full_pr_selection"]) is not bool:
            raise ValueError("rollback.force_full_pr_selection must be boolean")
        switch_path = _validate_relative_path(rollback["switch_path"], "rollback.switch_path")
        if not any(
            rule["decision"] == "full" and any(_matches(switch_path, pattern) for pattern in rule["patterns"])
            for rule in rules
        ):
            raise ValueError("rollback.switch_path must be covered by an explicit full rule")

        if require_catalog_scopes:
            missing = sorted(REQUIRED_CATALOG_SCOPES - set(scopes))
            if missing:
                raise ValueError(f"scope map is missing required catalog scopes {missing}")
            if set(always) != {"legacy-smoke", "smoke"}:
                raise ValueError("catalog always_run_scopes must contain exactly legacy-smoke and smoke")
            if full_scopes != ["all"]:
                raise ValueError("catalog full_scopes must be ['all']")
            if spec_root_value != "itambox/tests/e2e" or full_spec_path != "spec":
                raise ValueError("catalog spec_root/full_spec_path must be itambox/tests/e2e and spec")
            if scope_paths["all"] != full_spec_path:
                raise ValueError("special scope 'all' must map to the complete spec path")
            expected_shared = {
                "a11y": ("spec/accessibility", "accessibility"),
                "layout": ("spec/layout", "layout"),
                "legacy-smoke": ("spec/legacy-smoke", "legacy-smoke"),
                "smoke": ("spec/smoke", "smoke"),
            }
            for name, (path, owner) in expected_shared.items():
                entry = scopes[name]
                if entry["path"] != path or entry["kind"] != "qualification" or entry.get("owner") != owner:
                    raise ValueError(f"scope {name!r} must explicitly own {path}")
            for name in sorted(REQUIRED_APP_SCOPES):
                owner = name.split(":", 1)[1]
                entry = scopes[name]
                if entry["path"] != f"spec/apps/{owner}" or entry["kind"] != "app" or entry.get("owner") != owner:
                    raise ValueError(f"scope {name!r} must explicitly own spec/apps/{owner}")
            for name in sorted(REQUIRED_CONTRACT_SCOPES):
                owner = name.split(":", 1)[1]
                entry = scopes[name]
                if (
                    entry["path"] != f"spec/contracts/{owner}"
                    or entry["kind"] != "contract"
                    or entry.get("owner") != owner
                ):
                    raise ValueError(f"scope {name!r} must explicitly own spec/contracts/{owner}")
            owned_scope_names = sorted(REQUIRED_APP_SCOPES | REQUIRED_CONTRACT_SCOPES | REQUIRED_SHARED_SCOPES)
            for index, first_name in enumerate(owned_scope_names):
                first_path = scope_paths[first_name]
                for second_name in owned_scope_names[index + 1 :]:
                    second_path = scope_paths[second_name]
                    if (
                        first_path == second_path
                        or first_path.startswith(second_path + "/")
                        or second_path.startswith(first_path + "/")
                    ):
                        raise ValueError(
                            f"scope ownership paths overlap: {first_name!r}={first_path!r}, "
                            f"{second_name!r}={second_path!r}"
                        )
            if require_spec_evidence:
                ownership_groups = (
                    (REQUIRED_APP_SCOPES, full_path / "apps", "app"),
                    (REQUIRED_CONTRACT_SCOPES, full_path / "contracts", "contract"),
                )
                for owner_names, owner_root, owner_label in ownership_groups:
                    if not owner_root.is_dir():
                        raise ValueError(f"catalog {owner_label} spec root {owner_root} does not exist")
                    for spec in _discover_specs(owner_root):
                        owners = [
                            name
                            for name in owner_names
                            if spec == _contained_path(spec_root, scope_paths[name], f"scope {name!r} path")
                            or _contained_path(spec_root, scope_paths[name], f"scope {name!r} path") in spec.parents
                        ]
                        if len(owners) != 1:
                            relative = spec.relative_to(spec_root).as_posix()
                            raise ValueError(
                                f"{owner_label}-owned spec {relative!r} must have exactly one primary owner; "
                                f"found {sorted(owners)}"
                            )
            missing_rule_owners = sorted((REQUIRED_APP_SCOPES | REQUIRED_CONTRACT_SCOPES) - selected_scope_owners)
            if missing_rule_owners:
                raise ValueError(f"app/contract scopes have no explicit selection rule: {missing_rule_owners}")
        return document
    except ScopeMapError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ScopeMapError(str(exc)) from exc


def load_scope_map(
    path: str | Path,
    repo_root: str | Path,
    *,
    require_catalog_scopes: bool = True,
    require_spec_evidence: bool = True,
) -> dict[str, Any]:
    """Load a strict-JSON ``.yaml`` scope map and validate it."""

    try:
        document = load_json_file(path, label="E2E scope map")
    except SelectionError as exc:
        raise ScopeMapError(str(exc)) from exc
    return validate_scope_map(
        document,
        repo_root,
        require_catalog_scopes=require_catalog_scopes,
        require_spec_evidence=require_spec_evidence,
    )


def _normalise_change(value: ChangeRecord | Mapping[str, Any]) -> ChangeRecord:
    if isinstance(value, ChangeRecord):
        record = value
    elif isinstance(value, Mapping):
        allowed = {"status", "old_path", "new_path"}
        if not set(value).issubset(allowed) or "status" not in value:
            raise SelectionError("change records may contain only status, old_path, and new_path")
        raw_status = value["status"]
        if not isinstance(raw_status, str):
            raise SelectionError("change status must be a string")
        match = re.fullmatch(r"([AMDCR]|[RC]\d{1,3})", raw_status)
        if match is None:
            raise SelectionError(f"unsupported Git change status {raw_status!r}")
        if raw_status[0] in {"R", "C"} and len(raw_status) > 1 and int(raw_status[1:]) > 100:
            raise SelectionError(f"unsupported Git similarity score {raw_status!r}")
        status = raw_status[0]
        old_path = value.get("old_path")
        new_path = value.get("new_path")
        try:
            if old_path is not None:
                old_path = _validate_relative_path(old_path, "old changed path")
            if new_path is not None:
                new_path = _validate_relative_path(new_path, "new changed path")
        except ValueError as exc:
            raise SelectionError(str(exc)) from exc
        record = ChangeRecord(status=status, old_path=old_path, new_path=new_path)
    else:
        raise SelectionError("change records must be mappings or ChangeRecord values")

    expected = {
        "A": (False, True),
        "M": (False, True),
        "D": (True, False),
        "R": (True, True),
        "C": (True, True),
    }
    if record.status not in expected:
        raise SelectionError(f"unsupported Git change status {record.status!r}")
    has_old, has_new = record.old_path is not None, record.new_path is not None
    if (has_old, has_new) != expected[record.status]:
        raise SelectionError(f"status {record.status!r} has an invalid old/new path identity")
    try:
        if record.old_path is not None:
            _validate_relative_path(record.old_path, "old changed path")
        if record.new_path is not None:
            _validate_relative_path(record.new_path, "new changed path")
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    return record


def parse_name_status_z(data: bytes) -> list[ChangeRecord]:
    """Parse NUL-delimited ``git --name-status -z -M -C`` output."""

    if not isinstance(data, bytes):
        raise SelectionError("Git name-status input must be bytes")
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise SelectionError("Git name-status output is not NUL terminated")
    try:
        tokens = data.decode("utf-8", errors="strict").split("\0")
    except UnicodeDecodeError as exc:
        raise SelectionError("Git path output is not valid UTF-8") from exc
    tokens.pop()
    result: list[ChangeRecord] = []
    index = 0
    while index < len(tokens):
        status_token = tokens[index]
        index += 1
        match = re.fullmatch(r"([AMD]|[RC]\d{1,3})", status_token)
        if match is None:
            raise SelectionError(f"unsupported or malformed Git status {status_token!r}")
        if status_token[0] in {"R", "C"} and int(status_token[1:]) > 100:
            raise SelectionError(f"unsupported Git similarity score {status_token!r}")
        status = status_token[0]
        path_count = 2 if status in {"R", "C"} else 1
        if index + path_count > len(tokens):
            raise SelectionError(f"Git status {status_token!r} is missing a path identity")
        paths = tokens[index : index + path_count]
        index += path_count
        if status == "D":
            raw = {"status": status, "old_path": paths[0]}
        elif status in {"R", "C"}:
            raw = {"status": status, "old_path": paths[0], "new_path": paths[1]}
        else:
            raw = {"status": status, "new_path": paths[0]}
        result.append(_normalise_change(raw))
    return result


def changed_path_digest(changes: Iterable[ChangeRecord | Mapping[str, Any]]) -> str:
    """Hash a canonical sorted status/old/new change identity list."""

    records = sorted(
        (_normalise_change(change) for change in changes),
        key=lambda row: (row.status, row.old_path or "", row.new_path or ""),
    )
    payload = json.dumps(
        [record.as_dict() for record in records],
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _rule_specificity(rule: Mapping[str, Any], path: str) -> tuple[int, int]:
    matching = [pattern for pattern in rule["patterns"] if _matches(path, pattern)]
    if not matching:
        return (-1, -1)
    return max((len(_literal_prefix(pattern)), len(pattern)) for pattern in matching)


def _classify_identity(
    document: Mapping[str, Any],
    path: str,
    status: str,
    *,
    old_path: str | None,
    new_path: str | None,
) -> dict[str, Any]:
    matches = [rule for rule in document["rules"] if any(_matches(path, pattern) for pattern in rule["patterns"])]
    priority = {"safe_ignore": 1, "selected": 2, "full": 3}
    if matches:
        highest = max(priority[rule["decision"]] for rule in matches)
        candidates = [rule for rule in matches if priority[rule["decision"]] == highest]
        best_specificity = max(_rule_specificity(rule, path) for rule in candidates)
        winners = [rule for rule in candidates if _rule_specificity(rule, path) == best_specificity]
        signatures = {(rule["decision"], tuple(rule.get("scopes", []))) for rule in winners}
        if len(signatures) != 1:
            raise SelectionError(f"path {path!r} has ambiguous primary classifications")
        rule = sorted(winners, key=lambda item: item["id"])[0]
        reason: dict[str, Any] = {
            "matched_rule": rule["id"],
            "new_path": new_path,
            "old_path": old_path,
            "path": path,
            "status": status,
        }
        if rule["decision"] == "full":
            reason["escalation"] = "full"
        elif rule["decision"] == "safe_ignore":
            reason["safe_ignore"] = True
        else:
            reason["selected"] = sorted(rule["scopes"])
        return reason

    known = any(_matches(path, pattern) for pattern in document["known_production_roots"])
    return {
        "escalation": "full",
        "matched_rule": "unknown-production" if known else "unknown-path",
        "new_path": new_path,
        "old_path": old_path,
        "path": path,
        "status": status,
    }


def _selection_reason_sort_key(reason: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(reason.get("path", "")), str(reason.get("status", "")), str(reason.get("matched_rule", "")))


def build_selection(
    document: Mapping[str, Any],
    repo_root: str | Path,
    *,
    event_name: str,
    base_sha: str,
    head_sha: str,
    merge_base_sha: str,
    changes: Iterable[ChangeRecord | Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and validate a deterministic selection from already-resolved inputs."""

    try:
        if event_name not in KNOWN_EVENTS:
            raise ValueError(f"unsupported E2E event {event_name!r}")
        _validate_sha(base_sha, "base_sha")
        _validate_sha(head_sha, "head_sha")
        _validate_sha(merge_base_sha, "merge_base_sha")
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc

    normalized = sorted(
        (_normalise_change(change) for change in changes),
        key=lambda row: (row.status, row.old_path or "", row.new_path or ""),
    )
    identities = [(record.status, path) for record in normalized for path in record.identities()]
    if len(identities) != len(set(identities)):
        raise SelectionError("change set contains duplicate status/path identities")
    digest = changed_path_digest(normalized)

    reasons: list[dict[str, Any]] = []
    for record in normalized:
        for path in record.identities():
            reasons.append(
                _classify_identity(
                    document,
                    path,
                    record.status,
                    old_path=record.old_path,
                    new_path=record.new_path,
                )
            )
    reasons.sort(key=_selection_reason_sort_key)
    rollback_full = bool(document["rollback"]["force_full_pr_selection"] and event_name in SELECTIVE_EVENTS)
    authoritative = event_name in AUTHORITATIVE_FULL_EVENTS
    empty_diff = not normalized
    if empty_diff and event_name in SELECTIVE_EVENTS and not (base_sha == head_sha == merge_base_sha):
        raise SelectionError("an empty pull-request diff is inconsistent with distinct base/head identity")

    if rollback_full:
        reasons = [
            {
                "escalation": "full",
                "matched_rule": "rollback-force-full",
                "new_path": record.new_path,
                "old_path": record.old_path,
                "path": path,
                "status": record.status,
            }
            for record in normalized
            for path in record.identities()
        ]
        reasons.sort(key=_selection_reason_sort_key)
        mode = "full"
    elif authoritative or empty_diff or any(reason.get("escalation") == "full" for reason in reasons):
        mode = "full"
    elif reasons and all(reason.get("safe_ignore") is True for reason in reasons):
        mode = "none"
    else:
        mode = "selected"

    if mode == "full":
        scopes = sorted(document["full_scopes"])
        spec_paths = [document["full_spec_path"]]
    elif mode == "none":
        scopes = []
        spec_paths = []
    else:
        scope_set = set(document["always_run_scopes"])
        for reason in reasons:
            scope_set.update(reason.get("selected", []))
        scopes = sorted(scope_set)
        spec_paths = sorted({document["scopes"][scope]["path"] for scope in scopes})

    selection = {
        "schema": SCHEMA,
        "mode": mode,
        "event_name": event_name,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base_sha": merge_base_sha,
        "changed_path_digest": digest,
        "scopes": scopes,
        "spec_paths": spec_paths,
        "reasons": sorted(reasons, key=_selection_reason_sort_key),
    }
    validate_selection(selection, repo_root, document)
    return selection


def _validate_reason(
    reason: Any,
    document: Mapping[str, Any],
    aggregate_scopes: set[str],
    *,
    allow_narrow_full_reason: bool = False,
) -> None:
    if not isinstance(reason, dict):
        raise ValueError("selection reasons must be objects")
    base_keys = {"new_path", "old_path", "path", "status", "matched_rule"}
    variants = ({"selected"}, {"safe_ignore"}, {"escalation"})
    extras = set(reason) - base_keys
    if extras not in variants or not base_keys.issubset(reason):
        raise ValueError("selection reason has invalid keys")
    _validate_relative_path(reason["path"], "selection reason path")
    for key in ("old_path", "new_path"):
        value = reason[key]
        if value is not None:
            _validate_relative_path(value, f"selection reason {key}")
    if reason["status"] not in CHANGE_STATUSES:
        raise ValueError("selection reason has invalid status")
    if reason["status"] in {"A", "M"} and reason["old_path"] is not None:
        raise ValueError("added/modified reason cannot have an old path")
    if reason["status"] == "D" and reason["new_path"] is not None:
        raise ValueError("deleted reason cannot have a new path")
    if reason["status"] in {"R", "C"} and (reason["old_path"] is None or reason["new_path"] is None):
        raise ValueError("rename/copy reason must have both old and new paths")
    if reason["path"] not in {reason["old_path"], reason["new_path"]}:
        raise ValueError("selection reason path must be one of its old/new identities")
    rule_ids = {rule["id"] for rule in document["rules"]} | {
        "unknown-production",
        "unknown-path",
        "rollback-force-full",
    }
    if reason["matched_rule"] not in rule_ids:
        raise ValueError(f"selection reason names unknown rule {reason['matched_rule']!r}")
    if extras == {"selected"}:
        selected = _validate_sorted_unique_strings(reason["selected"], "reason selected scopes", nonempty=True)
        if not allow_narrow_full_reason and not set(selected).issubset(aggregate_scopes):
            raise ValueError("selection reason names a scope absent from the aggregate selection")
    elif extras == {"safe_ignore"}:
        if reason["safe_ignore"] is not True:
            raise ValueError("safe-ignore reason must contain true")
    elif reason["escalation"] != "full":
        raise ValueError("full reason escalation must equal 'full'")


def validate_selection(selection: Any, repo_root: str | Path, document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate canonical selection structure, identities, modes, and spec paths."""

    try:
        if not isinstance(selection, dict):
            raise ValueError("selection must be a JSON object")
        _expect_exact_keys(selection, SELECTION_KEYS, "selection")
        if type(selection["schema"]) is not int or selection["schema"] != SCHEMA:
            raise ValueError(f"selection schema must be {SCHEMA}")
        mode = selection["mode"]
        if mode not in {"none", "selected", "full"}:
            raise ValueError(f"selection mode {mode!r} is unsupported")
        if selection["event_name"] not in KNOWN_EVENTS:
            raise ValueError(f"selection event {selection['event_name']!r} is unsupported")
        for key in ("base_sha", "head_sha", "merge_base_sha"):
            _validate_sha(selection[key], key)
        _validate_digest(selection["changed_path_digest"])
        scopes = _validate_sorted_unique_strings(selection["scopes"], "selection scopes")
        spec_paths = _validate_sorted_unique_strings(selection["spec_paths"], "selection spec_paths")
        unknown = sorted(set(scopes) - set(document["scopes"]) - set(document["full_scopes"]))
        if unknown:
            raise ValueError(f"selection contains unknown scopes {unknown}")

        reasons = _expect_list(selection["reasons"], "selection reasons")
        if reasons != sorted(reasons, key=_selection_reason_sort_key):
            raise ValueError("selection reasons must use canonical path/status/rule sorting")
        for reason in reasons:
            _validate_reason(reason, document, set(scopes), allow_narrow_full_reason=mode == "full")

        spec_root = _contained_path(Path(repo_root), document["spec_root"], "scope map spec_root")
        if mode == "none":
            if scopes or spec_paths:
                raise ValueError("none selection must have no scopes or spec paths")
            if any(reason.get("safe_ignore") is not True for reason in reasons):
                raise ValueError("none selection may contain only safe-ignore reasons")
        elif mode == "full":
            if scopes != sorted(document["full_scopes"]):
                raise ValueError("full selection must contain exactly the configured full scopes")
            if spec_paths != [document["full_spec_path"]]:
                raise ValueError("full selection must contain exactly the complete spec root")
            if not _discover_specs(_contained_path(spec_root, spec_paths[0], "full spec path")):
                raise ValueError("full selection has no discoverable spec evidence")
        else:
            if not scopes or not spec_paths:
                raise ValueError("selected mode requires nonempty scopes and spec paths")
            if not set(document["always_run_scopes"]).issubset(scopes):
                raise ValueError("selected mode is missing an always-run product scope")
            expected_paths = sorted({document["scopes"][scope]["path"] for scope in scopes})
            if spec_paths != expected_paths:
                raise ValueError("selected spec paths do not exactly match selected scope paths")
            for relative in spec_paths:
                _validate_relative_path(relative, "selected spec path")
                target = _contained_path(spec_root, relative, "selected spec path")
                if not _discover_specs(target):
                    raise ValueError(f"selected spec path {relative!r} has no discoverable tests")
            selected_reason_scopes = {scope for reason in reasons for scope in reason.get("selected", [])}
            if not selected_reason_scopes:
                raise ValueError("selected mode has no product classification reason")
        return selection
    except SelectionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SelectionError(str(exc)) from exc


def load_selection(path: str | Path, repo_root: str | Path, document: Mapping[str, Any]) -> dict[str, Any]:
    selection = load_json_file(path, label="E2E selection")
    validate_selection(selection, repo_root, document)
    if canonical_json(selection).encode("utf-8") != Path(path).read_bytes():
        raise SelectionError("E2E selection artifact is not canonical JSON")
    return selection


def _git(repo_root: Path, arguments: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise SelectionError(f"cannot execute Git: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        detail = message[0][:300] if message else "unknown Git error"
        raise SelectionError(f"Git command failed: {detail}")
    return result.stdout


def _resolve_commit(repo_root: Path, sha: str, label: str) -> str:
    try:
        _validate_sha(sha, label)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    resolved = _git(repo_root, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    try:
        value = resolved.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SelectionError(f"Git returned a non-ASCII {label}") from exc
    if value != sha:
        raise SelectionError(f"{label} did not resolve to the exact requested commit object")
    return value


def collect_git_changes(
    repo_root: str | Path,
    *,
    event_name: str,
    base_sha: str,
    head_sha: str,
    merge_base_sha: str | None = None,
) -> tuple[str, list[ChangeRecord]]:
    """Resolve exact commit objects and collect a merge-base-aware change set."""

    root = Path(repo_root).resolve()
    if event_name not in KNOWN_EVENTS:
        raise SelectionError(f"unsupported E2E event {event_name!r}")
    _resolve_commit(root, base_sha, "base_sha")
    _resolve_commit(root, head_sha, "head_sha")
    raw_merge = _git(root, ["merge-base", "--", base_sha, head_sha])
    try:
        actual_merge = raw_merge.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SelectionError("Git returned a non-ASCII merge base") from exc
    try:
        _validate_sha(actual_merge, "computed merge_base_sha")
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc
    if merge_base_sha is not None and merge_base_sha != actual_merge:
        raise SelectionError("provided merge_base_sha does not equal Git's exact merge base")
    _resolve_commit(root, actual_merge, "merge_base_sha")
    raw_changes = _git(root, ["diff", "--name-status", "-z", "-M", "-C", actual_merge, head_sha, "--"])
    return actual_merge, parse_name_status_z(raw_changes)


def _write_canonical(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(value)
    target.write_text(content, encoding="utf-8", newline="\n")
    if target.read_bytes() != content.encode("utf-8"):
        raise SelectionError(f"failed to verify canonical output at {target}")


def selection_summary(selection: Mapping[str, Any]) -> str:
    lines = [
        f"## E2E selection: {selection['mode']}",
        "",
        f"- Event: `{selection['event_name']}`",
        f"- Base: `{selection['base_sha']}`",
        f"- Head: `{selection['head_sha']}`",
        f"- Merge base: `{selection['merge_base_sha']}`",
        f"- Changed identities: {len(selection['reasons'])}",
        f"- Scopes: {', '.join(selection['scopes']) if selection['scopes'] else '(none)'}",
        f"- Spec paths: {', '.join(selection['spec_paths']) if selection['spec_paths'] else '(none)'}",
    ]
    if selection["reasons"]:
        lines.extend(["", "### Classification", ""])
        for reason in selection["reasons"]:
            if reason.get("escalation") == "full":
                outcome = "full"
            elif reason.get("safe_ignore") is True:
                outcome = "safe-ignore"
            else:
                outcome = ", ".join(reason["selected"])
            lines.append(f"- `{reason['status']} {reason['path']}` -> `{reason['matched_rule']}` ({outcome})")
    return "\n".join(lines) + "\n"


def _append_text(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _github_outputs(selection: Mapping[str, Any], artifact_path: Path) -> str:
    values = {
        "base_sha": selection["base_sha"],
        "changed_path_digest": selection["changed_path_digest"],
        "head_sha": selection["head_sha"],
        "merge_base_sha": selection["merge_base_sha"],
        "mode": selection["mode"],
        "scopes_json": json.dumps(selection["scopes"], separators=(",", ":")),
        "selection_artifact": artifact_path.as_posix(),
        "selection_valid": "true",
        "spec_paths_json": json.dumps(selection["spec_paths"], separators=(",", ":")),
    }
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select and validate fail-closed E2E scopes from exact Git base/head commits."
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument(
        "--scope-map",
        type=Path,
        default=Path("scripts/e2e_scope_map.yaml"),
        help="strict-JSON scope map (relative paths are resolved beneath --repo-root)",
    )
    parser.add_argument(
        "--event-name", required=True, choices=sorted(KNOWN_EVENTS), help="GitHub event policy identity"
    )
    parser.add_argument("--base-sha", required=True, help="exact 40-character base commit SHA")
    parser.add_argument("--head-sha", required=True, help="exact 40-character head commit SHA")
    parser.add_argument("--merge-base-sha", help="optional expected merge base; mismatch fails closed")
    parser.add_argument(
        "--output",
        "--selection-output",
        dest="output",
        type=Path,
        required=True,
        help="canonical selection JSON artifact to create",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        default=Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None,
        help="optional GitHub output file to append validated scalar projections",
    )
    parser.add_argument(
        "--github-step-summary",
        "--summary",
        dest="summary",
        type=Path,
        default=Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.environ.get("GITHUB_STEP_SUMMARY") else None,
        help="optional Markdown job-summary file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    scope_map_path = args.scope_map if args.scope_map.is_absolute() else repo_root / args.scope_map
    output_path = args.output if args.output.is_absolute() else repo_root / args.output
    try:
        scope_map = load_scope_map(scope_map_path, repo_root)
        merge_base, changes = collect_git_changes(
            repo_root,
            event_name=args.event_name,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            merge_base_sha=args.merge_base_sha,
        )
        selection = build_selection(
            scope_map,
            repo_root,
            event_name=args.event_name,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            merge_base_sha=merge_base,
            changes=changes,
        )
        _write_canonical(output_path, selection)
        load_selection(output_path, repo_root, scope_map)
        if args.github_output:
            _append_text(args.github_output, _github_outputs(selection, output_path))
        summary = selection_summary(selection)
        if args.summary:
            _append_text(args.summary, summary)
        print(summary, end="")
        return 0
    except (ScopeMapError, SelectionError, OSError) as exc:
        print(f"E2E scope selection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
