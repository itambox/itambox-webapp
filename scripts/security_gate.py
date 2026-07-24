#!/usr/bin/env python3
"""Apply ITAMbox security thresholds and governed suppressions to scanner JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}
MAX_SUPPRESSION_DAYS = 90
_OWNER_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]*/)?[A-Za-z0-9-]+$")
_REQUIRED_FIELDS = {
    "id",
    "tool",
    "finding",
    "reason",
    "owner",
    "scope",
    "review_on",
    "expires_on",
    "references",
}


class SecurityGateError(ValueError):
    """Raised when scanner input or suppression governance is invalid."""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    blocking: int
    suppressed: int
    counts: dict[str, int]


def _parse_date(value: Any, field: str, entry_id: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SecurityGateError(f"{entry_id}: {field} must be an ISO date") from exc


def load_suppressions(path: Path, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityGateError(f"cannot read suppression manifest: {exc}") from exc
    if set(data) != {"version", "suppressions"} or data.get("version") != 1:
        raise SecurityGateError("suppression manifest must use version 1")
    entries = data.get("suppressions")
    if not isinstance(entries, list):
        raise SecurityGateError("suppressions must be a list")

    seen: set[str] = set()
    for entry in entries:
        entry_id = _validate_suppression(entry, today)
        if entry_id in seen:
            raise SecurityGateError(f"duplicate suppression id {entry_id}")
        seen.add(entry_id)
    return entries


def _validate_suppression(entry: Any, today: date) -> str:
    if not isinstance(entry, dict):
        raise SecurityGateError("each suppression must be an object")
    unknown = set(entry) - _REQUIRED_FIELDS
    missing = _REQUIRED_FIELDS - set(entry)
    if missing:
        raise SecurityGateError(f"suppression missing {sorted(missing)[0]}")
    if unknown:
        raise SecurityGateError(f"suppression has unknown field {sorted(unknown)[0]}")
    entry_id = entry["id"]
    _validate_identity_fields(entry_id, entry)
    _validate_governance_dates(entry_id, entry, today)
    _validate_scope(entry_id, entry["tool"], entry["scope"])
    return entry_id


def _validate_identity_fields(entry_id: Any, entry: dict[str, Any]) -> None:
    if not isinstance(entry_id, str) or not entry_id:
        raise SecurityGateError("suppression id must be non-empty")
    if entry["tool"] not in {"trivy", "gitleaks"}:
        raise SecurityGateError(f"{entry_id}: unknown tool")
    if not isinstance(entry["finding"], str) or not entry["finding"]:
        raise SecurityGateError(f"{entry_id}: finding must be non-empty")
    if not isinstance(entry["reason"], str) or len(entry["reason"].strip()) < 20:
        raise SecurityGateError(f"{entry_id}: reason must explain the risk decision")
    if not isinstance(entry["owner"], str) or not _OWNER_RE.fullmatch(entry["owner"]):
        raise SecurityGateError(f"{entry_id}: owner must be a GitHub user or team handle")
    if not isinstance(entry["references"], list) or not entry["references"]:
        raise SecurityGateError(f"{entry_id}: references must be non-empty")


def _validate_governance_dates(entry_id: str, entry: dict[str, Any], today: date) -> None:
    review_on = _parse_date(entry["review_on"], "review_on", entry_id)
    expires_on = _parse_date(entry["expires_on"], "expires_on", entry_id)
    if review_on > expires_on:
        raise SecurityGateError(f"{entry_id}: review_on must not follow expires_on")
    if expires_on < today:
        raise SecurityGateError(f"{entry_id}: suppression is expired")
    if review_on < today:
        raise SecurityGateError(f"{entry_id}: review is overdue")
    if (expires_on - today).days > MAX_SUPPRESSION_DAYS:
        raise SecurityGateError(f"{entry_id}: expires_on must be within 90 days")


def _validate_scope(entry_id: str, tool: str, scope: Any) -> None:
    if not isinstance(scope, dict):
        raise SecurityGateError(f"{entry_id}: scope must be an object")
    required = (
        {"target", "package", "version"}
        if tool == "trivy"
        else {"fingerprint", "path", "rule"}
    )
    if set(scope) != required:
        raise SecurityGateError(f"{entry_id}: scope must contain exactly {sorted(required)}")
    if any(not isinstance(scope[field], str) or not scope[field] for field in required):
        raise SecurityGateError(f"{entry_id}: scope values must be non-empty")
    if tool == "gitleaks" and any("*" in scope[field] for field in required):
        raise SecurityGateError(f"{entry_id}: broad gitleaks scope is forbidden")


def _trivy_suppressed(finding: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    return any(
        entry["tool"] == "trivy"
        and entry["finding"] == finding["id"]
        and entry["scope"] == {
            "target": finding["target"],
            "package": finding["package"],
            "version": finding["version"],
        }
        for entry in entries
    )


def _gitleaks_suppressed(finding: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    scope = {
        "fingerprint": finding.get("Fingerprint", ""),
        "path": finding.get("File", ""),
        "rule": finding.get("RuleID", ""),
    }
    return any(
        entry["tool"] == "gitleaks"
        and entry["finding"] == scope["rule"]
        and entry["scope"] == scope
        for entry in entries
    )


def _parse_trivy_vulnerability(vulnerability: Any, target: str) -> dict[str, str]:
    required = ("VulnerabilityID", "PkgName", "InstalledVersion", "Severity")
    if (
        not isinstance(vulnerability, dict)
        or any(not isinstance(vulnerability.get(field), str) or not vulnerability[field] for field in required)
    ):
        raise SecurityGateError("invalid Trivy report")
    return {
        "id": vulnerability["VulnerabilityID"],
        "target": target,
        "package": vulnerability["PkgName"],
        "version": vulnerability["InstalledVersion"],
        "severity": vulnerability["Severity"].upper(),
    }


def _parse_trivy_report(report: Any) -> tuple[list[dict[str, str]], set[str]]:
    if (
        not isinstance(report, dict)
        or report.get("SchemaVersion") != 2
        or not isinstance(report.get("Results"), list)
        or not report["Results"]
    ):
        raise SecurityGateError("invalid Trivy report")
    findings: list[dict[str, str]] = []
    targets: set[str] = set()
    for result in report["Results"]:
        if not isinstance(result, dict) or not isinstance(result.get("Target"), str) or not result["Target"]:
            raise SecurityGateError("invalid Trivy report")
        target = result["Target"]
        targets.add(target)
        vulnerabilities = result.get("Vulnerabilities")
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise SecurityGateError("invalid Trivy report")
        findings.extend(_parse_trivy_vulnerability(item, target) for item in vulnerabilities)
    return findings, targets


def evaluate_trivy(
    reports: list[dict[str, Any]],
    suppressions: list[dict[str, Any]],
    sarif_path: Path,
    expected_targets: set[str] | None = None,
) -> GateResult:
    findings: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for report in reports:
        report_findings, report_targets = _parse_trivy_report(report)
        findings.extend(report_findings)
        seen_targets.update(report_targets)

    missing_targets = (expected_targets or set()) - seen_targets
    if missing_targets:
        missing = ", ".join(sorted(missing_targets))
        raise SecurityGateError(f"missing expected Trivy targets: {missing}")

    visible: list[dict[str, str]] = []
    suppressed = 0
    for finding in findings:
        if _trivy_suppressed(finding, suppressions):
            suppressed += 1
        else:
            visible.append(finding)
    counts = Counter(finding["severity"] for finding in visible)
    blocking = sum(counts[severity] for severity in BLOCKING_SEVERITIES)
    _write_sarif(sarif_path, visible)
    return GateResult(blocking == 0, blocking, suppressed, dict(counts))


def _write_sarif(path: Path, findings: list[dict[str, str]]) -> None:
    rules = {}
    results = []
    level = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning"}
    for finding in findings:
        rules.setdefault(finding["id"], {
            "id": finding["id"],
            "shortDescription": {"text": "Dependency vulnerability"},
        })
        results.append({
            "ruleId": finding["id"],
            "level": level.get(finding["severity"], "note"),
            "message": {"text": f"{finding['package']} {finding['version']} ({finding['severity']})"},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": finding["target"]}}}],
        })
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "ITAMbox security gate", "rules": list(rules.values())}},
            "results": results,
        }],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def evaluate_gitleaks(findings: list[dict[str, Any]], suppressions: list[dict[str, Any]]) -> GateResult:
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise SecurityGateError("invalid Gitleaks report")
    suppressed = sum(_gitleaks_suppressed(item, suppressions) for item in findings)
    blocking = len(findings) - suppressed
    return GateResult(blocking == 0, blocking, suppressed, {"SECRET": blocking})


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityGateError(f"cannot read scanner report: {exc}") from exc


def _print_result(name: str, result: GateResult) -> None:
    counts = " ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
    print(f"{name}: blocking={result.blocking} suppressed={result.suppressed} {counts}".rstrip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("security/suppressions.json"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    trivy = subparsers.add_parser("trivy")
    trivy.add_argument("--report", action="append", required=True, type=Path)
    trivy.add_argument("--sarif", required=True, type=Path)
    trivy.add_argument("--expect-target", action="append", default=[])
    gitleaks = subparsers.add_parser("gitleaks")
    gitleaks.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        suppressions = load_suppressions(args.manifest)
        if args.command == "validate":
            print(f"security suppressions valid: {len(suppressions)}")
            return 0
        if args.command == "trivy":
            result = evaluate_trivy(
                [_load_json(path) for path in args.report],
                suppressions,
                args.sarif,
                expected_targets=set(args.expect_target),
            )
            _print_result("dependency vulnerabilities", result)
        else:
            result = evaluate_gitleaks(_load_json(args.report), suppressions)
            _print_result("secret scan", result)
        return 0 if result.passed else 1
    except SecurityGateError as exc:
        print(f"security gate error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
