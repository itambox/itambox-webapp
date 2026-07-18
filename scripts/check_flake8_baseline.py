#!/usr/bin/env python
"""Ratchet-style flake8 baseline check.

itambox has ~4k pre-existing flake8 violations (see scripts/flake8_baseline.json)
that are impractical to fix in one pass. Rather than either ignoring lint
entirely or blocking all work until the backlog is cleared, this script makes
CI/pre-commit fail when the current per-file/error-code counts differ from the
checked-in baseline. Increases are regressions; decreases mean debt was fixed
and the baseline must be updated in the same change. Exact equality makes the
baseline a monotonic ratchet: removed debt cannot later be reintroduced inside
stale headroom.

Counting per (file, code) rather than per exact line keeps the baseline
stable across unrelated edits: inserting a line above an existing violation
elsewhere in the file would shift its line number under a line-based diff and
produce a false regression here it does not.

Usage:
    python scripts/check_flake8_baseline.py               # check (CI / pre-commit)
    python scripts/check_flake8_baseline.py --write-baseline   # regenerate after cleanup

The checked-in baseline is the union needed by the supported developer and CI
platforms. Regenerate it on Linux with the pinned Python/Flake8 versions used by
GitHub Actions; pycodestyle can report a small CRLF-sensitive subset differently
on Windows. A Windows-only regeneration can therefore make Linux CI fail.
"""
import argparse
import collections
from importlib.metadata import PackageNotFoundError, version
import json
import re
import subprocess
import sys
from pathlib import Path

VIOLATION_RE = re.compile(r"^(?P<path>.+?):\d+:\d+: (?P<code>[A-Z]+\d+) ")

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "flake8_baseline.json"
DEFAULT_TARGETS = ["itambox", "scripts"]
REQUIRED_TOOL_VERSIONS = {
    "flake8": "7.0.0",
    "flake8-bugbear": "24.12.12",
    "mccabe": "0.7.0",
    "pycodestyle": "2.11.1",
    "pyflakes": "3.2.0",
}
# pycodestyle 2.11.1 reports these existing operator/comma-spacing findings
# under Linux/LF but not under Windows/CRLF. Linux CI remains canonical and
# requires exact equality; Windows may be short by only this reviewed amount.
WINDOWS_CRLF_SHORTFALL = {
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


def parse_counts(output):
    counts = collections.Counter()
    examples = {}
    for line in output.splitlines():
        match = VIOLATION_RE.match(line)
        if not match:
            continue
        path = Path(match.group("path")).as_posix()
        key = (path, match.group("code"))
        counts[key] += 1
        examples.setdefault(key, line)
    return counts, examples


def load_baseline(baseline_path):
    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline = {}
    for key, count in raw.items():
        path, code = key.rsplit("\t", 1)
        baseline[(path, code)] = count
    return baseline


def write_baseline(counts, baseline_path):
    data = {f"{path}\t{code}": count for (path, code), count in sorted(counts.items())}
    baseline_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(data)} baseline entries ({sum(counts.values())} total violations) to {baseline_path}")


def validate_flake8_result(output, error_output, status):
    """Return parsed findings or a fail-closed process status."""
    if error_output.strip():
        # Flake8 diagnostics use stdout. Any stderr means a tool/plugin/config
        # problem, even when stdout also contains baseline-covered findings.
        print(output)
        print(error_output, file=sys.stderr)
        print("flake8 wrote unexpected stderr; refusing to pass", file=sys.stderr)
        return None, None, 2
    if status not in (0, 1):
        print(output)
        print(f"flake8 exited with unexpected status {status}", file=sys.stderr)
        return None, None, status

    counts, examples = parse_counts(output)
    if status == 1 and not counts:
        print(output)
        print("flake8 failed without any parseable violations; refusing to pass", file=sys.stderr)
        return None, None, 1
    return counts, examples, None


def compare_baseline(counts, baseline):
    regressions = [
        (path, code, baseline.get((path, code), 0), count)
        for (path, code), count in counts.items()
        if count > baseline.get((path, code), 0)
    ]
    stale_entries = []
    for (path, code), expected in baseline.items():
        current = counts.get((path, code), 0)
        allowed_shortfall = WINDOWS_CRLF_SHORTFALL.get((path, code), 0) if sys.platform == "win32" else 0
        if current < expected - allowed_shortfall:
            stale_entries.append((path, code, expected, current))
    return regressions, stale_entries


def report_mismatches(regressions, stale_entries, examples, baseline_path):
    if stale_entries:
        print("flake8 baseline is stale -- removed violation(s) must update it:\n")
        for path, code, before, after in sorted(stale_entries):
            print(f"  {path}: {code} count {before} -> {after}")
        print()
    if regressions:
        print("flake8 baseline exceeded -- new violation(s) introduced:\n")
        for path, code, before, after in sorted(regressions):
            print(f"  {path}: {code} count {before} -> {after}")
            print(f"    e.g. {examples[(path, code)]}")
    print(
        "Regenerate the baseline on Linux after fixing regressions with "
        "`python scripts/check_flake8_baseline.py --write-baseline`; "
        f"include the reviewed {baseline_path} diff in this change."
    )
    return 1


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate the baseline file from the current flake8 output instead of checking it.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Path to the baseline JSON file (default: scripts/flake8_baseline.json).",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=REPO_ROOT,
        help="Directory flake8 is invoked from (default: repo root).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    toolchain_problems = verify_toolchain()
    if toolchain_problems:
        print("flake8 toolchain mismatch; refusing to run an incomplete policy:", file=sys.stderr)
        for problem in toolchain_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    output, error_output, flake8_status = run_flake8(args.targets, args.cwd)
    counts, examples, failure_status = validate_flake8_result(output, error_output, flake8_status)
    if failure_status is not None:
        return failure_status

    if args.write_baseline:
        if sys.platform == "win32" and args.baseline.resolve() == BASELINE_PATH.resolve():
            print("Refusing canonical baseline regeneration on Windows; use the pinned Linux toolchain.", file=sys.stderr)
            return 2
        write_baseline(counts, args.baseline)
        return 0

    regressions, stale_entries = compare_baseline(counts, load_baseline(args.baseline))
    if regressions or stale_entries:
        return report_mismatches(regressions, stale_entries, examples, args.baseline)
    print(f"flake8: {sum(counts.values())} violation(s) match the monotonic baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
