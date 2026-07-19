#!/usr/bin/env python
"""Fail-closed, identity-based Flake8 debt ratchet.

ITAMbox has roughly 4k pre-existing Flake8 findings. The checked-in baseline
records the identity of each reviewed finding as path, code, message, and source
line. Physical row/column numbers are deliberately excluded: inserting an
unrelated line above existing debt must not create a false regression.

A new identity is always a regression, even if it replaces an old finding with
the same error code in the same file. Removed identities make the baseline stale
and require a reviewed cleanup update, so fixed debt never becomes headroom.

The canonical baseline is generated with Python 3.12 and the pinned Flake8
toolchain. Python 3.11 tokenizes f-string expressions differently and therefore
omits ten known findings; that reviewed compatibility shortfall is keyed to the
interpreter version, never the operating system.
"""
import argparse
import ast
import collections
from importlib.metadata import PackageNotFoundError, version
import json
import re
import subprocess
import sys
from pathlib import Path

VIOLATION_RE = re.compile(
    r"^(?P<path>.+?):(?P<row>\d+):(?P<column>\d+): "
    r"(?P<code>[A-Z]+\d+) (?P<message>.*)$"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "flake8_baseline.json"
DEFAULT_TARGETS = ["itambox", "scripts"]
BASELINE_SCHEMA_VERSION = 2
CANONICAL_PYTHON = (3, 12)
REQUIRED_TOOL_VERSIONS = {
    "flake8": "7.0.0",
    "flake8-bugbear": "24.12.12",
    "mccabe": "0.7.0",
    "pycodestyle": "2.11.1",
    "pyflakes": "3.2.0",
}
# Python 3.11 treats each f-string as one STRING token. Python 3.12 exposes the
# inner operators/commas to pycodestyle. CI on canonical Python 3.12 enforces
# every identity; supported Python 3.11 developer hooks may be short only by
# these reviewed path/code counts. New identities are never exempted.
PYTHON311_FSTRING_SHORTFALL = {
    ("itambox/assets/tests/test_requests.py", "E226"): 4,
    ("itambox/core/importers/snipeit.py", "E231"): 4,
    ("itambox/core/reports/charts.py", "E226"): 2,
}


def verify_toolchain():
    problems = []
    for distribution, expected in REQUIRED_TOOL_VERSIONS.items():
        try:
            actual = version(distribution)
        except PackageNotFoundError:
            problems.append(f"{distribution} is not installed (expected {expected})")
            continue
        if actual != expected:
            problems.append(f"{distribution}=={actual} (expected {expected})")
    return problems


def run_flake8(targets, cwd):
    result = subprocess.run(
        [sys.executable, "-m", "flake8", *targets],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.stdout, result.stderr, result.returncode


def _normalise_path(raw_path, cwd):
    path = Path(raw_path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(cwd.resolve())
        except ValueError:
            pass
    return path.as_posix()


def _source_anchor(code, source_text, source_lines, syntax_tree, row, path):
    """Return a row-stable source anchor for one finding.

    Python 3.11 and 3.12 attribute B907 on a multiline f-string to different
    physical lines. Anchor B907 to the smallest enclosing statement so both
    interpreters identify the same debt without weakening code/message checks.
    Other checks keep their exact reported source line.
    """
    if code != "B907":
        return source_lines[row - 1]
    candidates = [
        node
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.stmt)
        and node.lineno <= row <= getattr(node, "end_lineno", node.lineno)
    ]
    if not candidates:
        raise ValueError(f"cannot anchor B907 row {row} to a statement in {path}")
    statement = min(
        candidates,
        key=lambda node: (
            getattr(node, "end_lineno", node.lineno) - node.lineno,
            getattr(node, "end_col_offset", 0) - node.col_offset,
        ),
    )
    source = ast.get_source_segment(source_text, statement)
    if source is None:
        source = "\n".join(
            source_lines[
                statement.lineno - 1 : getattr(
                    statement,
                    "end_lineno",
                    statement.lineno,
                )
            ]
        )
    return source


def parse_findings(output, cwd):
    findings = collections.Counter()
    examples = {}
    source_cache = {}
    for line in output.splitlines():
        match = VIOLATION_RE.match(line)
        if not match:
            continue
        path = _normalise_path(match.group("path"), cwd)
        source_path = Path(path)
        if not source_path.is_absolute():
            source_path = cwd / source_path
        source_path = source_path.resolve()
        if source_path not in source_cache:
            try:
                source_text = source_path.read_text(encoding="utf-8")
                source_cache[source_path] = (
                    source_text,
                    source_text.splitlines(),
                    ast.parse(source_text, filename=path),
                )
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"cannot read finding source {path}: {exc}") from exc
            except SyntaxError as exc:
                raise ValueError(f"cannot parse finding source {path}: {exc}") from exc
        row = int(match.group("row"))
        source_text, source_lines, syntax_tree = source_cache[source_path]
        if row < 1 or row > len(source_lines):
            raise ValueError(
                f"finding row {row} is outside {path} ({len(source_lines)} lines)"
            )
        code = match.group("code")
        message = match.group("message")
        key = (
            path,
            code,
            message,
            _source_anchor(
                code,
                source_text,
                source_lines,
                syntax_tree,
                row,
                path,
            ),
        )
        findings[key] += 1
        examples.setdefault(key, line)
    return findings, examples


def load_baseline(baseline_path):
    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"expected Flake8 baseline schema {BASELINE_SCHEMA_VERSION}"
        )
    if raw.get("canonical_python") != "3.12":
        raise ValueError("baseline canonical_python must be '3.12'")
    rows = raw.get("findings")
    if not isinstance(rows, list):
        raise ValueError("baseline findings must be a list")

    baseline = collections.Counter()
    required = {"path", "code", "message", "source", "count"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError(f"baseline finding {index} has invalid fields")
        count = row["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"baseline finding {index} has invalid count")
        values = (row["path"], row["code"], row["message"], row["source"])
        if not all(isinstance(value, str) for value in values):
            raise ValueError(f"baseline finding {index} has non-string identity")
        if values in baseline:
            raise ValueError(f"baseline finding {index} duplicates an identity")
        baseline[values] = count
    return baseline


def write_baseline(findings, baseline_path):
    rows = [
        {
            "path": path,
            "code": code,
            "message": message,
            "source": source,
            "count": count,
        }
        for (path, code, message, source), count in sorted(findings.items())
    ]
    data = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "canonical_python": "3.12",
        "findings": rows,
    }
    baseline_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(rows)} baseline identities "
        f"({sum(findings.values())} total violations) to {baseline_path}"
    )


def validate_flake8_result(output, error_output, status, cwd):
    """Return parsed findings or a fail-closed process status."""
    if error_output.strip():
        print(output)
        print(error_output, file=sys.stderr)
        print("flake8 wrote unexpected stderr; refusing to pass", file=sys.stderr)
        return None, None, 2
    if status not in (0, 1):
        print(output)
        print(f"flake8 exited with unexpected status {status}", file=sys.stderr)
        return None, None, status

    try:
        findings, examples = parse_findings(output, cwd)
    except ValueError as exc:
        print(output)
        print(f"could not fingerprint flake8 output: {exc}", file=sys.stderr)
        return None, None, 2
    if status == 1 and not findings:
        print(output)
        print("flake8 failed without any parseable violations; refusing to pass", file=sys.stderr)
        return None, None, 1
    return findings, examples, None


def _apply_python311_shortfall(stale_entries):
    if sys.version_info[:2] >= CANONICAL_PYTHON:
        return stale_entries
    remaining = stale_entries.copy()
    budgets = PYTHON311_FSTRING_SHORTFALL.copy()
    for key in sorted(stale_entries):
        path_code = key[:2]
        allowed = min(remaining[key], budgets.get(path_code, 0))
        if allowed:
            remaining[key] -= allowed
            budgets[path_code] -= allowed
            if remaining[key] == 0:
                del remaining[key]
    return remaining


def _python311_compatible_identities(findings):
    """Collapse only B907's interpreter-dependent physical source attribution.

    Canonical Python 3.12 remains fully source-identity strict. Python 3.11 can
    report the same B907 expression against another line of a multiline f-string;
    there we retain path/code/message/count identity and let 3.12 CI enforce the
    exact source anchor.
    """
    if sys.version_info[:2] >= CANONICAL_PYTHON:
        return findings
    compatible = collections.Counter()
    for (path, code, message, source), count in findings.items():
        if code == 'B907':
            source = '<python-3.11-b907-source>'
        compatible[(path, code, message, source)] += count
    return compatible


def compare_baseline(findings, baseline, *, allow_python311_shortfall=True):
    compared_findings = _python311_compatible_identities(findings)
    compared_baseline = _python311_compatible_identities(baseline)
    regressions = compared_findings - compared_baseline
    stale_entries = compared_baseline - compared_findings
    if allow_python311_shortfall:
        stale_entries = _apply_python311_shortfall(stale_entries)
    return regressions, stale_entries


def _counts_by_file_code(findings):
    counts = collections.Counter()
    for (path, code, _message, _source), count in findings.items():
        counts[(path, code)] += count
    return counts


def report_mismatches(
    regressions,
    stale_entries,
    examples,
    baseline,
    findings,
    baseline_path,
):
    before = _counts_by_file_code(baseline)
    after = _counts_by_file_code(findings)
    if stale_entries:
        print("flake8 baseline is stale -- removed violation(s) must update it:\n")
        for key, count in sorted(stale_entries.items()):
            path, code, message, source = key
            print(
                f"  {path}: {code} count {before[(path, code)]} -> "
                f"{after[(path, code)]} ({count} removed identity occurrence(s))"
            )
            print(f"    {message} | {source.strip()}")
        print()
    if regressions:
        print("flake8 baseline exceeded -- new violation identity/identities introduced:\n")
        for key, count in sorted(regressions.items()):
            path, code, _message, _source = key
            print(
                f"  {path}: {code} count {before[(path, code)]} -> "
                f"{after[(path, code)]} ({count} new identity occurrence(s))"
            )
            example = examples.get(key)
            if example is None:
                example = next(
                    (
                        line
                        for candidate, line in examples.items()
                        if candidate[:3] == key[:3]
                    ),
                    f"{path}: {code} {key[2]}",
                )
            print(f"    e.g. {example}")
    print(
        "Fix new identities first. After cleanup, regenerate on canonical "
        "Python 3.12 with `python scripts/check_flake8_baseline.py "
        f"--write-baseline` and review the {baseline_path} diff."
    )
    return 1


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Update the baseline after cleanup; new identities are refused.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Path to the baseline JSON file.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=REPO_ROOT,
        help="Directory Flake8 is invoked from.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    toolchain_problems = verify_toolchain()
    if toolchain_problems:
        print(
            "flake8 toolchain mismatch; refusing to run an incomplete policy:",
            file=sys.stderr,
        )
        for problem in toolchain_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    try:
        baseline = load_baseline(args.baseline)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"invalid flake8 baseline {args.baseline}: {exc}", file=sys.stderr)
        return 2

    output, error_output, flake8_status = run_flake8(args.targets, args.cwd)
    findings, examples, failure_status = validate_flake8_result(
        output,
        error_output,
        flake8_status,
        args.cwd,
    )
    if failure_status is not None:
        return failure_status

    if args.write_baseline:
        if sys.version_info[:2] != CANONICAL_PYTHON:
            print(
                "Refusing canonical baseline regeneration outside Python 3.12.",
                file=sys.stderr,
            )
            return 2
        regressions, _stale = compare_baseline(
            findings,
            baseline,
            allow_python311_shortfall=False,
        )
        if regressions:
            return report_mismatches(
                regressions,
                collections.Counter(),
                examples,
                baseline,
                findings,
                args.baseline,
            )
        write_baseline(findings, args.baseline)
        return 0

    regressions, stale_entries = compare_baseline(findings, baseline)
    if regressions or stale_entries:
        return report_mismatches(
            regressions,
            stale_entries,
            examples,
            baseline,
            findings,
            args.baseline,
        )
    print(
        f"flake8: {sum(findings.values())} violation(s) match the "
        "identity baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
