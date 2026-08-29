#!/usr/bin/env python
"""Evaluate the stable aggregate E2E gate from detector/execution evidence.

Malformed outer input is a usage error.  Once the envelope is structurally
usable, every missing artifact, invalid selection/identity, cancelled job, or
failed certification becomes an explicit failing verdict rather than an
exception which a workflow might accidentally interpret as a skip.
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
except ImportError:  # Direct ``python scripts/check_e2e_gate.py`` invocation.
    import select_e2e_scopes as selector

CONTROL_RE = selector.CONTROL_RE
DIGEST_RE = selector.DIGEST_RE
KNOWN_EVENTS = selector.KNOWN_EVENTS
SCHEMA = selector.SCHEMA
SHA_RE = selector.SHA_RE
RUNTIME_KEYS = frozenset({"tested_checkout_sha", "tested_checkout_kind"})
SELECTION_KEYS = selector.SELECTION_KEYS
SelectionError = selector.SelectionError
canonical_json = selector.canonical_json
load_json_file = selector.load_json_file


TOP_KEYS = frozenset({"schema", "detector", "execution", "certification", "current"})
IDENTITY_KEYS = frozenset({"event_name", "base_sha", "head_sha", "merge_base_sha", "changed_path_digest"})
DETECTOR_KEYS = frozenset({"result", "selection", "artifact_exists"})
EXECUTION_KEYS = frozenset(
    {
        "result",
        "selection",
        "tested_checkout_sha",
        "selection_artifact_exists",
        "discovery_artifact_exists",
        "report_artifact_exists",
        "certification_artifact_exists",
    }
)
JOB_RESULTS = frozenset({"success", "failure", "cancelled", "skipped", "timed_out"})
CHANGE_STATUSES = frozenset({"A", "M", "D", "R", "C"})
RULE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9:-]*\Z")


class GateInputError(ValueError):
    """The aggregate gate input envelope is structurally unusable."""


class _InvalidEvidence(ValueError):
    pass


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise GateInputError(f"{label} has invalid keys; missing={missing}, unknown={unknown}")


def _identity(value: Any, label: str, *, allow_runtime: bool = False) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _InvalidEvidence(f"{label} is not a complete identity object")
    allowed = IDENTITY_KEYS | (RUNTIME_KEYS if allow_runtime else set())
    missing = sorted(IDENTITY_KEYS - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        raise _InvalidEvidence(f"{label} is not a complete identity object: missing={missing}, unknown={unknown}")
    if value["event_name"] not in KNOWN_EVENTS:
        raise _InvalidEvidence(f"{label} has an unsupported event")
    for key in ("base_sha", "head_sha", "merge_base_sha"):
        if not isinstance(value[key], str) or SHA_RE.fullmatch(value[key]) is None:
            raise _InvalidEvidence(f"{label}.{key} is malformed")
    if not isinstance(value["changed_path_digest"], str) or DIGEST_RE.fullmatch(value["changed_path_digest"]) is None:
        raise _InvalidEvidence(f"{label}.changed_path_digest is malformed")
    if allow_runtime and "tested_checkout_sha" in value:
        if not isinstance(value["tested_checkout_sha"], str) or SHA_RE.fullmatch(value["tested_checkout_sha"]) is None:
            raise _InvalidEvidence(f"{label}.tested_checkout_sha is malformed")
        if value.get("tested_checkout_kind") not in {"head", "merge_candidate"}:
            raise _InvalidEvidence(f"{label}.tested_checkout_kind is malformed")
    return {key: value[key] for key in IDENTITY_KEYS}


def _runtime_checkout(value: Mapping[str, Any], selection: Mapping[str, Any]) -> str:
    checkout = value.get("tested_checkout_sha", selection["head_sha"])
    kind = value.get("tested_checkout_kind", "head")
    if checkout != selection["head_sha"] and kind != "merge_candidate":
        raise _InvalidEvidence("a checkout different from the selected head must be marked merge_candidate")
    return checkout


def _sorted_unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise _InvalidEvidence(f"{label} must be a list")
    if any(not isinstance(item, str) or not item or CONTROL_RE.search(item) for item in value):
        raise _InvalidEvidence(f"{label} must contain control-free nonblank strings")
    if value != sorted(value) or len(value) != len(set(value)):
        raise _InvalidEvidence(f"{label} must be sorted and unique")
    return value


def _basic_reasons(value: Any, mode: str, scopes: set[str], label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _InvalidEvidence(f"{label} must be a list")
    previous: tuple[str, str, str] | None = None
    for index, reason in enumerate(value):
        if not isinstance(reason, dict):
            raise _InvalidEvidence(f"{label}[{index}] must be an object")
        base = {"new_path", "old_path", "path", "status", "matched_rule"}
        extra = set(reason) - base
        if not base.issubset(reason) or extra not in ({"selected"}, {"safe_ignore"}, {"escalation"}):
            raise _InvalidEvidence(f"{label}[{index}] has invalid keys")
        path = _nonblank_reason_path(reason["path"], f"{label}[{index}].path")
        status = reason["status"]
        rule = reason["matched_rule"]
        if status not in CHANGE_STATUSES or not isinstance(rule, str) or RULE_TOKEN_RE.fullmatch(rule) is None:
            raise _InvalidEvidence(f"{label}[{index}] has an invalid status or rule token")
        for identity_key in ("old_path", "new_path"):
            identity = reason[identity_key]
            if identity is not None:
                identity = _nonblank_reason_path(identity, f"{label}[{index}].{identity_key}")
            if reason["status"] in {"A", "M"} and identity_key == "old_path" and identity is not None:
                raise _InvalidEvidence(f"{label}[{index}] added/modified reason has old_path")
            if reason["status"] == "D" and identity_key == "new_path" and identity is not None:
                raise _InvalidEvidence(f"{label}[{index}] deleted reason has new_path")
        if status in {"R", "C"} and (reason["old_path"] is None or reason["new_path"] is None):
            raise _InvalidEvidence(f"{label}[{index}] rename/copy reason lacks an identity")
        if path not in {reason["old_path"], reason["new_path"]}:
            raise _InvalidEvidence(f"{label}[{index}] path is not one of old/new identities")
        key = (path, status, rule)
        if previous is not None and key < previous:
            raise _InvalidEvidence(f"{label} must use canonical path/status/rule sorting")
        previous = key
        if extra == {"selected"}:
            selected = _sorted_unique_strings(reason["selected"], f"{label}[{index}].selected")
            if not selected or (mode != "full" and not set(selected).issubset(scopes)):
                raise _InvalidEvidence(f"{label}[{index}] has invalid selected scopes")
        elif extra == {"safe_ignore"}:
            if reason["safe_ignore"] is not True:
                raise _InvalidEvidence(f"{label}[{index}] safe_ignore must be true")
        elif reason["escalation"] != "full":
            raise _InvalidEvidence(f"{label}[{index}] escalation must equal full")
    if mode == "none" and any(reason.get("safe_ignore") is not True for reason in value):
        raise _InvalidEvidence("none selection may contain only safe-ignore reasons")
    return value


def _nonblank_reason_path(value: Any, label: str) -> str:
    path = value
    if not isinstance(path, str) or not path or CONTROL_RE.search(path):
        raise _InvalidEvidence(f"{label} must be a control-free path")
    if "\\" in path or path.startswith("/") or re.match(r"[A-Za-z]:", path):
        raise _InvalidEvidence(f"{label} must be a POSIX repository-relative path")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise _InvalidEvidence(f"{label} is not normalized")
    return path


def _basic_selection(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(SELECTION_KEYS):
        raise _InvalidEvidence(f"{label} is not a complete selection object")
    if type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise _InvalidEvidence(f"{label} has an unsupported schema")
    mode = value["mode"]
    if mode not in {"none", "selected", "full"}:
        raise _InvalidEvidence(f"{label} has an unsupported mode")
    _identity({key: value[key] for key in IDENTITY_KEYS}, f"{label} identity")
    scopes = _sorted_unique_strings(value["scopes"], f"{label}.scopes")
    paths = _sorted_unique_strings(value["spec_paths"], f"{label}.spec_paths")
    _basic_reasons(value["reasons"], mode, set(scopes), f"{label}.reasons")
    if mode == "none" and (scopes or paths):
        raise _InvalidEvidence("none selection must not contain scopes/spec paths")
    if mode == "full" and (scopes != ["all"] or paths != ["spec"]):
        raise _InvalidEvidence("full selection must be exactly scopes=['all'], spec_paths=['spec']")
    if mode == "selected":
        if not scopes or not paths:
            raise _InvalidEvidence("selected selection must contain scopes/spec paths")
        if not value["reasons"]:
            raise _InvalidEvidence("selected selection must contain classification reasons")
        if not {"legacy-smoke", "smoke"}.issubset(scopes):
            raise _InvalidEvidence("selected selection is missing always-run product scopes")
        for path in paths:
            parts = path.split("/")
            if (
                "\\" in path
                or not (path == "spec" or path.startswith("spec/"))
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
                or re.match(r"[A-Za-z]:", path)
            ):
                raise _InvalidEvidence(f"selected spec path {path!r} is unsafe")
    return value


def _failed(mode: str | None, *reasons: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "success": False,
        "verdict": "failed",
        "mode": mode,
        "reasons": sorted(set(reasons)),
    }


def evaluate_gate(value: Any) -> dict[str, Any]:
    """Evaluate one aggregate gate envelope without performing I/O."""

    if not isinstance(value, dict):
        raise GateInputError("gate input must be a JSON object")
    _exact_keys(value, TOP_KEYS, "gate input")
    if type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise GateInputError(f"gate input schema must be {SCHEMA}")
    detector = value["detector"]
    if not isinstance(detector, dict):
        raise GateInputError("gate detector must be an object")
    _exact_keys(detector, DETECTOR_KEYS, "gate detector")
    if detector["result"] not in JOB_RESULTS:
        raise GateInputError("gate detector result is unsupported")
    if type(detector["artifact_exists"]) is not bool:
        raise GateInputError("gate detector artifact_exists must be boolean")

    if detector["result"] != "success":
        return _failed(None, f"detector job result was {detector['result']}")
    if not detector["artifact_exists"]:
        return _failed(None, "detector selection artifact is absent")
    try:
        selection = _basic_selection(detector["selection"], "detector selection")
        current = _identity(value["current"], "current identity", allow_runtime=True)
        runtime_checkout_sha = _runtime_checkout(value["current"], selection)
    except _InvalidEvidence as exc:
        return _failed(None, str(exc))
    mode = selection["mode"]
    selected_identity = {key: selection[key] for key in IDENTITY_KEYS}
    if current != selected_identity:
        return _failed(mode, "current identity does not match detector selection")

    execution = value["execution"]
    if not isinstance(execution, dict) or "result" not in execution:
        raise GateInputError("gate execution must be an object with a result")
    if execution["result"] not in JOB_RESULTS:
        raise GateInputError("gate execution result is unsupported")

    if mode == "none":
        if set(execution) != {"result"}:
            return _failed(mode, "none selection has unexpected execution evidence")
        if execution["result"] != "skipped":
            return _failed(mode, f"none selection unexpectedly ran execution ({execution['result']})")
        if value["certification"] is not None:
            return _failed(mode, "none selection unexpectedly produced certification")
        return {"schema": SCHEMA, "success": True, "verdict": "passed", "mode": mode, "reasons": []}

    if set(execution) != set(EXECUTION_KEYS):
        return _failed(mode, "selected/full execution envelope is incomplete or has unknown fields")
    if execution["result"] != "success":
        return _failed(mode, f"required execution job result was {execution['result']}")
    artifact_fields = (
        "selection_artifact_exists",
        "discovery_artifact_exists",
        "report_artifact_exists",
        "certification_artifact_exists",
    )
    for field in artifact_fields:
        if type(execution[field]) is not bool:
            return _failed(mode, f"execution {field} flag is malformed")
        if not execution[field]:
            return _failed(mode, f"required execution artifact is absent: {field}")
    try:
        execution_selection = _basic_selection(execution["selection"], "execution selection")
    except _InvalidEvidence as exc:
        return _failed(mode, str(exc))
    if execution_selection != selection:
        return _failed(mode, "execution selection artifact differs from detector selection")
    if not isinstance(execution["tested_checkout_sha"], str) or not SHA_RE.fullmatch(execution["tested_checkout_sha"]):
        return _failed(mode, "execution tested checkout SHA is malformed")
    if execution["tested_checkout_sha"] != runtime_checkout_sha:
        return _failed(mode, "execution tested checkout does not match current event checkout")

    certification = value["certification"]
    if not isinstance(certification, dict):
        return _failed(mode, "certification artifact is missing or malformed")
    required_certification = {"schema", "success", "verdict", *IDENTITY_KEYS}
    if not required_certification.issubset(certification):
        return _failed(mode, "certification artifact is incomplete")
    if type(certification["schema"]) is not int or certification["schema"] != SCHEMA:
        return _failed(mode, "certification schema is unsupported")
    if certification["success"] is not True or certification["verdict"] != "passed":
        return _failed(mode, "certification did not pass")
    try:
        certification_identity = _identity(
            {key: certification[key] for key in IDENTITY_KEYS}, "certification identity"
        )
    except _InvalidEvidence as exc:
        return _failed(mode, str(exc))
    if certification_identity != selected_identity or certification_identity != current:
        return _failed(mode, "certification identity does not match selection/current event")
    if "tested_checkout_sha" in certification and certification["tested_checkout_sha"] != runtime_checkout_sha:
        return _failed(mode, "certification tested checkout does not match current event checkout")
    if "tested_checkout_kind" in certification and certification["tested_checkout_kind"] != value["current"].get("tested_checkout_kind", "head"):
        return _failed(mode, "certification tested checkout kind does not match current event")
    return {"schema": SCHEMA, "success": True, "verdict": "passed", "mode": mode, "reasons": []}


def gate_summary(result: Mapping[str, Any]) -> str:
    lines = [f"## E2E gate: {result['verdict']}", "", f"- Mode: `{result.get('mode') or 'unavailable'}`"]
    if result["reasons"]:
        lines.extend(["", "### Reasons", ""])
        lines.extend(f"- {reason}" for reason in result["reasons"])
    return "\n".join(lines) + "\n"


def _write(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(result)
    path.write_text(content, encoding="utf-8", newline="\n")
    if path.read_bytes() != content.encode("utf-8"):
        raise GateInputError(f"could not verify canonical gate output at {path}")


def _append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed aggregate E2E gate for detector, execution, artifact, and certification evidence."
    )
    parser.add_argument("--input", type=Path, required=True, help="strict-JSON aggregate gate envelope")
    parser.add_argument("--output", type=Path, help="optional canonical gate verdict artifact")
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
    try:
        try:
            value = load_json_file(args.input, label="E2E gate input")
        except SelectionError as exc:
            raise GateInputError(str(exc)) from exc
        result = evaluate_gate(value)
        if args.output:
            _write(args.output, result)
        summary = gate_summary(result)
        if args.summary:
            _append(args.summary, summary)
        stream = sys.stdout if result["success"] else sys.stderr
        print(summary, end="", file=stream)
        return 0 if result["success"] else 1
    except (GateInputError, OSError) as exc:
        print(f"E2E aggregate gate input failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
