#!/usr/bin/env python
"""Certify that Playwright discovery and execution match an E2E selection.

Certification is deliberately independent of Playwright's implementation.  It
consumes small JSON envelopes emitted by the repository wrapper and refuses to
turn missing, malformed, focused, incomplete, or identity-mismatched evidence
into a successful artifact.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import select_e2e_scopes as selector
except ImportError:  # Direct ``python scripts/certify_e2e_run.py`` invocation.
    import select_e2e_scopes as selector

CONTROL_RE = selector.CONTROL_RE
DIGEST_RE = selector.DIGEST_RE
KNOWN_EVENTS = selector.KNOWN_EVENTS
SCHEMA = selector.SCHEMA
SHA_RE = selector.SHA_RE
SPEC_SUFFIXES = selector.SPEC_SUFFIXES
ScopeMapError = selector.ScopeMapError
SelectionError = selector.SelectionError
canonical_json = selector.canonical_json
load_json_file = selector.load_json_file
load_selection = selector.load_selection
load_scope_map = selector.load_scope_map
validate_selection = selector.validate_selection


IDENTITY_KEYS = frozenset({"event_name", "base_sha", "head_sha", "merge_base_sha", "changed_path_digest"})
DISCOVERY_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "selection_identity",
        "tested_checkout_sha",
        "selected_spec_paths",
        "discovered_specs",
        "discovered_tests",
        "setup_projects",
        "focused",
    }
)
DISCOVERY_OPTIONAL_KEYS = frozenset({"only", "registered_spec_paths"})
EXECUTION_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "selection_identity",
        "tested_checkout_sha",
        "selected_spec_paths",
        "executed_specs",
        "executed_tests",
        "cleanup",
        "focused",
        "report",
    }
)
EXECUTION_OPTIONAL_KEYS = frozenset({"only"})
DISCOVERED_TEST_KEYS = frozenset({"id", "spec", "project"})
EXECUTED_TEST_KEYS = frozenset({"id", "spec", "project", "status", "attempts"})
ATTEMPT_KEYS = frozenset({"retry", "status", "identity"})
CLEANUP_KEYS = frozenset({"success", "failures"})
REQUIRED_SETUP_PROJECTS = ["setup-admin", "setup-aggregate", "setup-operator", "setup-viewer"]
ATTEMPT_STATUSES = frozenset({"passed", "failed", "timed_out", "interrupted"})


class CertificationError(ValueError):
    """The available discovery/execution evidence cannot certify the run."""


def _exact_keys(value: Mapping[str, Any], required: frozenset[str], optional: frozenset[str], label: str) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise CertificationError(f"{label} has invalid keys: {'; '.join(details)}")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CertificationError(f"{label} must be a JSON object")
    return value


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or CONTROL_RE.search(value):
        raise CertificationError(f"{label} must be a nonblank control-free string")
    return value


def _sorted_unique_strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise CertificationError(f"{label} must be a{' nonempty' if nonempty else ''} list")
    for item in value:
        _nonblank(item, f"{label} entry")
    if value != sorted(value):
        raise CertificationError(f"{label} must use canonical lexical sorting")
    if len(value) != len(set(value)):
        raise CertificationError(f"{label} must not contain duplicates")
    return value


def _identity(value: Any, label: str, *, allow_runtime: bool = False) -> dict[str, str]:
    result = _object(value, label)
    allowed = IDENTITY_KEYS | ({"tested_checkout_sha", "tested_checkout_kind"} if allow_runtime else set())
    actual = set(result)
    missing = sorted(IDENTITY_KEYS - actual)
    unknown = sorted(actual - allowed)
    if missing or unknown:
        raise CertificationError(f"{label} has invalid keys; missing={missing}, unknown={unknown}")
    event_name = result["event_name"]
    if event_name not in KNOWN_EVENTS:
        raise CertificationError(f"{label}.event_name is unsupported")
    for key in ("base_sha", "head_sha", "merge_base_sha"):
        if not isinstance(result[key], str) or not SHA_RE.fullmatch(result[key]):
            raise CertificationError(f"{label}.{key} is not a canonical Git SHA")
    digest = result["changed_path_digest"]
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise CertificationError(f"{label}.changed_path_digest is not canonical")
    if allow_runtime and "tested_checkout_sha" in result:
        if not isinstance(result["tested_checkout_sha"], str) or not SHA_RE.fullmatch(result["tested_checkout_sha"]):
            raise CertificationError(f"{label}.tested_checkout_sha is not a canonical Git SHA")
        if result.get("tested_checkout_kind") not in {"head", "merge_candidate"}:
            raise CertificationError(f"{label}.tested_checkout_kind is unsupported")
    return {key: result[key] for key in IDENTITY_KEYS}


def _runtime_checkout(current: Mapping[str, Any], selection: Mapping[str, Any]) -> tuple[str, str]:
    checkout = current.get("tested_checkout_sha", selection["head_sha"])
    kind = current.get("tested_checkout_kind", "head")
    if checkout != selection["head_sha"] and kind != "merge_candidate":
        raise CertificationError("a checkout different from the PR head must be marked merge_candidate")
    if checkout == selection["head_sha"] and kind not in {"head", "merge_candidate"}:
        raise CertificationError("tested checkout kind is invalid")
    return checkout, kind


def _selection_identity(selection: Mapping[str, Any]) -> dict[str, str]:
    return {key: selection[key] for key in sorted(IDENTITY_KEYS)}


def _relative_spec_path(value: Any, label: str) -> str:
    path = _nonblank(value, label)
    if "\\" in path or path.startswith("/") or re.match(r"[A-Za-z]:", path):
        raise CertificationError(f"{label} must be a POSIX path relative to the E2E package")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CertificationError(f"{label} is not normalized")
    if not path.startswith("spec/") or not path.endswith(SPEC_SUFFIXES):
        raise CertificationError(f"{label} must name a .spec.ts or .test.ts file beneath spec/")
    return path


def _is_within(path: str, selected: str) -> bool:
    return path == selected or path.startswith(selected.rstrip("/") + "/")


def _resolve_under(root: Path, relative: str, label: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CertificationError(f"{label} resolves outside the E2E package") from exc
    return target


def _filesystem_specs(e2e_root: Path, selected_paths: list[str]) -> list[str]:
    found: set[str] = set()
    for selected in selected_paths:
        target = _resolve_under(e2e_root, selected, "selected spec path")
        if target.is_file():
            candidates = [target] if target.name.endswith(SPEC_SUFFIXES) else []
        elif target.is_dir():
            candidates = [
                path for path in target.rglob("*") if path.is_file() and path.name.endswith(SPEC_SUFFIXES)
            ]
        else:
            candidates = []
        for candidate in candidates:
            found.add(candidate.relative_to(e2e_root).as_posix())
    return sorted(found)


def _validate_test_rows(value: Any, label: str, *, execution: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CertificationError(f"{label} must be a nonempty list")
    expected_keys = EXECUTED_TEST_KEYS if execution else DISCOVERED_TEST_KEYS
    result: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    previous: tuple[str, str, str] | None = None
    for index, raw in enumerate(value):
        row = _object(raw, f"{label}[{index}]")
        _exact_keys(row, expected_keys, frozenset(), f"{label}[{index}]")
        test_id = _nonblank(row["id"], f"{label}[{index}].id")
        spec = _relative_spec_path(row["spec"], f"{label}[{index}].spec")
        project = _nonblank(row["project"], f"{label}[{index}].project")
        identity = (spec, project, test_id)
        if identity in identities:
            raise CertificationError(f"{label} contains duplicate test identity {identity!r}")
        if previous is not None and identity < previous:
            raise CertificationError(f"{label} must use canonical spec/project/id sorting")
        identities.add(identity)
        previous = identity
        result.append(row)
    return result


def _validate_attempts(
    test: Mapping[str, Any],
    label: str,
    globally_seen: set[str],
    *,
    allow_legacy_identity: bool,
) -> int:
    attempts = test["attempts"]
    if not isinstance(attempts, list) or not attempts:
        raise CertificationError(f"{label}.attempts must be a nonempty list")
    attempt_identities: set[str] = set()
    for index, raw in enumerate(attempts):
        attempt = _object(raw, f"{label}.attempts[{index}]")
        _exact_keys(attempt, ATTEMPT_KEYS, frozenset(), f"{label}.attempts[{index}]")
        retry = attempt["retry"]
        if type(retry) is not int or retry != index:
            raise CertificationError(f"{label} retry numbers must be the contiguous sequence starting at zero")
        status = attempt["status"]
        if status not in ATTEMPT_STATUSES:
            raise CertificationError(f"{label} attempt {index} has unsupported status {status!r}")
        identity = attempt["identity"]
        if identity is None and allow_legacy_identity and len(attempts) == 1 and retry == 0:
            continue
        identity = _nonblank(identity, f"{label} attempt {index} identity")
        if re.search(rf"(?:^|-)r{retry}(?:-|$)", identity) is None:
            raise CertificationError(f"{label} attempt {index} identity does not contain distinct r{retry} evidence")
        if identity in attempt_identities or identity in globally_seen:
            raise CertificationError(f"{label} reuses retry identity {identity!r}")
        attempt_identities.add(identity)
        globally_seen.add(identity)
        if index < len(attempts) - 1 and status == "passed":
            raise CertificationError(f"{label} retried after a passing attempt")
    if attempts[-1]["status"] != test["status"]:
        raise CertificationError(f"{label} final attempt status disagrees with final test status")
    return len(attempts) - 1


def certify_run(
    selection: Any,
    discovery: Any,
    execution: Any,
    current_identity: Any,
    repo_root: str | Path,
    scope_map: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a canonical passing certification or raise ``CertificationError``."""

    try:
        if selection is None:
            raise CertificationError("selection report is missing")
        if discovery is None:
            raise CertificationError("discovery report is missing")
        if execution is None:
            raise CertificationError("execution report is missing")
        validate_selection(selection, repo_root, scope_map)
    except SelectionError as exc:
        raise CertificationError(f"selection is invalid: {exc}") from exc

    if selection["mode"] not in {"selected", "full"}:
        raise CertificationError("only selected/full execution can be certified")
    expected_identity = _selection_identity(selection)
    current = _identity(current_identity, "current identity", allow_runtime=True)
    if current != expected_identity:
        raise CertificationError("current event identity does not match the selection")
    runtime_checkout_sha, runtime_checkout_kind = _runtime_checkout(current_identity, selection)

    discovery = _object(discovery, "discovery envelope")
    _exact_keys(discovery, DISCOVERY_REQUIRED_KEYS, DISCOVERY_OPTIONAL_KEYS, "discovery envelope")
    if type(discovery["schema"]) is not int or discovery["schema"] != SCHEMA:
        raise CertificationError(f"discovery schema must be {SCHEMA}")
    if _identity(discovery["selection_identity"], "discovery selection identity") != expected_identity:
        raise CertificationError("discovery identity does not match the selection")
    if discovery["tested_checkout_sha"] != runtime_checkout_sha or not SHA_RE.fullmatch(
        str(discovery["tested_checkout_sha"])
    ):
        raise CertificationError("discovery tested checkout does not match selection head")
    if type(discovery["focused"]) is not bool or type(discovery.get("only", False)) is not bool:
        raise CertificationError("discovery focused/only flags must be boolean")
    if discovery["focused"] or discovery.get("only", False):
        raise CertificationError("focused/only Playwright tests cannot be certified")
    selected_paths = _sorted_unique_strings(
        discovery["selected_spec_paths"], "discovery selected_spec_paths", nonempty=True
    )
    if selected_paths != selection["spec_paths"]:
        raise CertificationError("discovery selected paths do not match the selection")
    setup_projects = _sorted_unique_strings(discovery["setup_projects"], "discovery setup_projects", nonempty=True)
    if setup_projects != REQUIRED_SETUP_PROJECTS:
        raise CertificationError(f"discovery must include exactly the setup projects {REQUIRED_SETUP_PROJECTS}")

    discovered_specs = _sorted_unique_strings(discovery["discovered_specs"], "discovered_specs", nonempty=True)
    for index, spec in enumerate(discovered_specs):
        _relative_spec_path(spec, f"discovered_specs[{index}]")
        if not any(_is_within(spec, selected) for selected in selected_paths):
            raise CertificationError(f"discovered spec {spec!r} is outside the selection")
    discovered_tests = _validate_test_rows(discovery["discovered_tests"], "discovered_tests", execution=False)
    discovered_spec_set = set(discovered_specs)
    if any(test["spec"] not in discovered_spec_set for test in discovered_tests):
        raise CertificationError("a discovered test names a spec absent from discovered_specs")
    specs_with_tests = {test["spec"] for test in discovered_tests}
    if specs_with_tests != discovered_spec_set:
        raise CertificationError("every discovered spec must contain at least one discovered test")
    for selected in selected_paths:
        if not any(_is_within(test["spec"], selected) for test in discovered_tests):
            raise CertificationError(f"selected path {selected!r} discovered no tests")

    e2e_root = (Path(repo_root).resolve() / scope_map["spec_root"]).resolve()
    expected_filesystem_specs = _filesystem_specs(e2e_root, selected_paths)
    if discovered_specs != expected_filesystem_specs:
        missing = sorted(set(expected_filesystem_specs) - set(discovered_specs))
        unexpected = sorted(set(discovered_specs) - set(expected_filesystem_specs))
        raise CertificationError(
            "discovery does not equal the selected on-disk spec tree; "
            f"missing={missing[:5]}, unexpected={unexpected[:5]}"
        )

    execution = _object(execution, "execution envelope")
    _exact_keys(execution, EXECUTION_REQUIRED_KEYS, EXECUTION_OPTIONAL_KEYS, "execution envelope")
    report = _object(execution["report"], "Playwright report metadata")
    _exact_keys(report, frozenset({"file", "malformed", "error"}), frozenset(), "Playwright report metadata")
    if not isinstance(report["file"], str) or not report["file"]:
        raise CertificationError("Playwright report metadata has no report filename")
    if report["malformed"] is not False or report["error"] is not None:
        raise CertificationError("Playwright JSON report is missing or malformed")
    if type(execution["schema"]) is not int or execution["schema"] != SCHEMA:
        raise CertificationError(f"execution schema must be {SCHEMA}")
    if _identity(execution["selection_identity"], "execution selection identity") != expected_identity:
        raise CertificationError("execution identity does not match the selection")
    if execution["tested_checkout_sha"] != runtime_checkout_sha or not SHA_RE.fullmatch(
        str(execution["tested_checkout_sha"])
    ):
        raise CertificationError("execution tested checkout does not match selection head")
    if type(execution["focused"]) is not bool or type(execution.get("only", False)) is not bool:
        raise CertificationError("execution focused/only flags must be boolean")
    if execution["focused"] or execution.get("only", False):
        raise CertificationError("focused/only execution cannot be certified")
    execution_paths = _sorted_unique_strings(
        execution["selected_spec_paths"], "execution selected_spec_paths", nonempty=True
    )
    if execution_paths != selected_paths:
        raise CertificationError("execution selected paths do not match discovery")
    executed_specs = _sorted_unique_strings(execution["executed_specs"], "executed_specs", nonempty=True)
    if executed_specs != discovered_specs:
        raise CertificationError("executed spec set does not exactly match discovered specs")
    executed_tests = _validate_test_rows(execution["executed_tests"], "executed_tests", execution=True)
    discovered_identities = {(row["spec"], row["project"], row["id"]) for row in discovered_tests}
    executed_identities = {(row["spec"], row["project"], row["id"]) for row in executed_tests}
    if executed_identities != discovered_identities:
        raise CertificationError("executed test identities do not exactly match discovered tests")

    retry_count = 0
    attempt_identities: set[str] = set()
    for index, test in enumerate(executed_tests):
        status = test["status"]
        if status != "passed":
            raise CertificationError(f"executed test {test['id']!r} has non-passing final status {status!r}")
        retry_count += _validate_attempts(
            test,
            f"executed_tests[{index}]",
            attempt_identities,
            allow_legacy_identity=test["spec"].startswith(
                (
                    "spec/legacy-smoke/",
                    "spec/layout/",
                    "spec/accessibility/",
                    "spec/regressions/",
                    "spec/external/",
                )
            ),
        )

    cleanup = _object(execution["cleanup"], "execution cleanup")
    _exact_keys(cleanup, CLEANUP_KEYS, frozenset(), "execution cleanup")
    if type(cleanup["success"]) is not bool or not isinstance(cleanup["failures"], list):
        raise CertificationError("execution cleanup success/failures types are invalid")
    for failure in cleanup["failures"]:
        _nonblank(failure, "execution cleanup failure")
    if not cleanup["success"] or cleanup["failures"]:
        raise CertificationError("execution cleanup evidence is not successful and empty")

    return {
        "schema": SCHEMA,
        "success": True,
        "verdict": "passed",
        **expected_identity,
        "tested_checkout_sha": runtime_checkout_sha,
        "tested_checkout_kind": runtime_checkout_kind,
        "selection_mode": selection["mode"],
        "selected_spec_path_count": len(selected_paths),
        "discovered_spec_count": len(discovered_specs),
        "discovered_test_count": len(discovered_tests),
        "executed_spec_count": len(executed_specs),
        "executed_test_count": len(executed_tests),
        "retry_count": retry_count,
        "cleanup_success": True,
    }


def certification_summary(result: Mapping[str, Any]) -> str:
    return (
        "## E2E certification: passed\n\n"
        f"- Selection mode: `{result['selection_mode']}`\n"
        f"- Tested checkout: `{result['tested_checkout_sha']}`\n"
        f"- Selected paths: {result['selected_spec_path_count']}\n"
        f"- Discovered: {result['discovered_spec_count']} specs / {result['discovered_test_count']} tests\n"
        f"- Executed: {result['executed_spec_count']} specs / {result['executed_test_count']} tests\n"
        f"- Retries: {result['retry_count']}\n"
        "- Cleanup: passed\n"
    )


def _write_canonical(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(value)
    path.write_text(content, encoding="utf-8", newline="\n")
    if path.read_bytes() != content.encode("utf-8"):
        raise CertificationError(f"failed to verify canonical certification at {path}")


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _load_required(path: Path, label: str) -> Any:
    try:
        return load_json_file(path, label=label)
    except SelectionError as exc:
        raise CertificationError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Certify canonical Playwright discovery/execution evidence against an E2E selection."
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--scope-map", type=Path, default=Path("scripts/e2e_scope_map.yaml"), help="strict-JSON map")
    parser.add_argument("--selection", type=Path, required=True, help="canonical selector artifact")
    parser.add_argument("--discovery", type=Path, required=True, help="Playwright discovery envelope")
    parser.add_argument(
        "--execution", "--report", dest="execution", type=Path, required=True, help="execution envelope"
    )
    parser.add_argument("--current-identity", type=Path, help="current event identity JSON")
    parser.add_argument("--event-name", "--current-event-name", dest="event_name", choices=sorted(KNOWN_EVENTS))
    parser.add_argument("--base-sha", "--current-base-sha", dest="base_sha")
    parser.add_argument("--head-sha", "--current-head-sha", dest="head_sha")
    parser.add_argument("--merge-base-sha", "--current-merge-base-sha", dest="merge_base_sha")
    parser.add_argument("--changed-path-digest", "--current-changed-path-digest", dest="changed_path_digest")
    parser.add_argument("--output", type=Path, required=True, help="canonical certification artifact to create")
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
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()

    def beneath_root(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    try:
        scope_map = load_scope_map(beneath_root(args.scope_map), root)
        selection = load_selection(beneath_root(args.selection), root, scope_map)
        discovery = _load_required(beneath_root(args.discovery), "E2E discovery envelope")
        execution = _load_required(beneath_root(args.execution), "E2E execution envelope")
        if args.current_identity:
            current = _load_required(beneath_root(args.current_identity), "current E2E identity")
            if any((args.event_name, args.base_sha, args.head_sha, args.merge_base_sha, args.changed_path_digest)):
                raise CertificationError("use --current-identity or individual current identity flags, not both")
        else:
            values = (args.event_name, args.base_sha, args.head_sha, args.merge_base_sha, args.changed_path_digest)
            if any(value is None for value in values):
                raise CertificationError(
                    "current identity requires --event-name, --base-sha, --head-sha, --merge-base-sha, "
                    "and --changed-path-digest"
                )
            current = {
                "event_name": args.event_name,
                "base_sha": args.base_sha,
                "head_sha": args.head_sha,
                "merge_base_sha": args.merge_base_sha,
                "changed_path_digest": args.changed_path_digest,
            }
        result = certify_run(selection, discovery, execution, current, root, scope_map)
        output = beneath_root(args.output)
        _write_canonical(output, result)
        # A second strict parse detects accidental non-canonical/truncated writes.
        if (
            _load_required(output, "E2E certification") != result
            or output.read_bytes() != canonical_json(result).encode("utf-8")
        ):
            raise CertificationError("written certification did not round-trip canonically")
        summary = certification_summary(result)
        if args.summary:
            _append(args.summary, summary)
        print(summary, end="")
        return 0
    except (CertificationError, ScopeMapError, SelectionError, OSError) as exc:
        print(f"E2E certification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
