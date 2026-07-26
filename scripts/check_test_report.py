#!/usr/bin/env python
"""Test-reporting ratchet: durations published, silent non-execution refused.

The coverage gates answer "was this code exercised". This gate answers the
question that comes before it -- "did the suite actually run" -- by reading the
JUnit XML the pytest run writes, and it publishes the duration data the project
needs to keep the serial suite viable.

It fails closed on every shape where a green tick would be misleading:

* A missing, unparseable, or empty report is a failure, not a pass. A run that
  collected nothing must never be reported as a run in which nothing broke.
* Recorded failures or errors fail the gate even though pytest's own exit code
  already did. That redundancy is the point: it is the check that still fires if
  a future workflow edit swallows an exit code behind ``|| true`` or
  ``continue-on-error``.
* Skipped tests are ratcheted against ``MAX_SKIPPED_TESTS``. A skip is a test
  that silently did not run, so raising the allowance is a reviewed code change
  with a stated reason -- never something a branch can do to itself.
* The number of tests that actually ran is ratcheted against the reviewed
  baseline in ``scripts/suite_baseline.json``. "More than zero tests ran" is a
  far weaker claim than it looks: a collection error in one package, a renamed
  directory that ``python_files`` no longer matches, or a ``conftest.py`` that
  drops a whole tree all leave a large, green, and quietly smaller suite. Growth
  is free and needs no update; a fall is review-required, and a legitimate one
  (tests genuinely deleted) is recorded with ``--write-baseline
  --allow-decline --reason "..."``.

Durations are reported, never enforced: wall-clock on a shared CI runner is too
noisy to gate on, and a flaky timing gate teaches people to ignore gates. The
slowest tests are published to the job summary and the full report is uploaded
as an artifact so the cost of the serial suite stays visible while #21 works
towards safe parallel execution.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, not the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.coverage_policy import (  # noqa: E402  -- deliberate, see the bootstrap above
    CANONICAL_PYTHON,
    COVERAGE_ROOT,
    REPO_ROOT,
    PolicyError,
    verify_baseline_write_environment,
    write_summary,
)

DEFAULT_REPORT = REPO_ROOT / COVERAGE_ROOT / "junit.xml"
DEFAULT_SUITE_BASELINE = REPO_ROOT / "scripts" / "suite_baseline.json"

SUITE_SCHEMA_VERSION = 1

# The whole suite must run. This is not a budget to spend: every skip is a test
# that did not execute, so raising this number requires naming which tests are
# allowed to skip and why, in review, in this file.
MAX_SKIPPED_TESTS = 0

# Published for visibility only -- crossing it never fails the gate.
SLOW_TEST_SECONDS = 5.0
DEFAULT_SLOWEST = 20


class TestCase:
    def __init__(self, name, classname, time, outcome, message=""):
        self.name = name
        self.classname = classname
        self.time = time
        self.outcome = outcome  # passed | failed | error | skipped
        self.message = message

    @property
    def label(self):
        return f"{self.classname}::{self.name}" if self.classname else self.name


def load_report(report_path):
    """Parse a pytest JUnit XML report, failing closed on anything unusable.

    The report is a trusted artifact produced by the run this gate is checking;
    it is never fetched from an untrusted source.
    """
    path = Path(report_path)
    try:
        tree = ElementTree.parse(path)
    except FileNotFoundError as exc:
        raise PolicyError(
            f"no test report at {path}; the gate cannot certify a run it cannot see "
            "(produce one with `pytest --junitxml=junit.xml`)"
        ) from exc
    except (OSError, ElementTree.ParseError) as exc:
        raise PolicyError(f"cannot parse test report {path}: {exc}") from exc

    cases = []
    for element in tree.getroot().iter("testcase"):
        outcome = "passed"
        message = ""
        for tag in ("failure", "error", "skipped"):
            child = element.find(tag)
            if child is not None:
                outcome = "error" if tag == "error" else ("failed" if tag == "failure" else "skipped")
                message = (child.get("message") or "").strip()
                break
        try:
            duration = float(element.get("time") or 0.0)
        except ValueError:
            duration = 0.0
        cases.append(
            TestCase(
                name=element.get("name") or "<unnamed>",
                classname=element.get("classname") or "",
                time=duration,
                outcome=outcome,
                message=message,
            )
        )
    if not cases:
        raise PolicyError(
            f"test report {path} contains no test cases; a suite that collected nothing "
            "must not be reported as a suite that passed"
        )
    return cases


def read_suite_baseline(baseline_path):
    """Load the reviewed suite size, failing closed on anything unusable."""
    path = Path(baseline_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(
            f"no suite baseline at {path}; record one from a clean run with "
            "`python scripts/check_test_report.py --write-baseline`"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read suite baseline {path}: {exc}") from exc

    required = {"schema_version", "canonical_python", "executed_tests"}
    optional = {"decline_justification"}
    if not isinstance(raw, dict) or not required <= set(raw) or not set(raw) <= (required | optional):
        raise PolicyError("suite baseline has invalid top-level fields")
    if raw["schema_version"] != SUITE_SCHEMA_VERSION:
        raise PolicyError(f"expected suite baseline schema {SUITE_SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"suite baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    executed = raw["executed_tests"]
    if isinstance(executed, bool) or not isinstance(executed, int) or executed <= 0:
        raise PolicyError("suite baseline 'executed_tests' must be a positive integer")
    return raw


def write_suite_baseline(baseline_path, executed, justification=None):
    data = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "executed_tests": executed,
    }
    if justification is not None:
        data["decline_justification"] = justification
    Path(baseline_path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote suite baseline to {baseline_path}: {executed} executed test(s).")


def handle_write(args, executed):
    """Record a new suite size, refusing an unjustified shrink."""
    justification = None
    if Path(args.suite_baseline).exists():
        recorded = read_suite_baseline(args.suite_baseline)["executed_tests"]
        if executed < recorded:
            if not args.allow_decline or not args.reason:
                print(
                    f"Refusing to record a smaller suite without an explicit justification: "
                    f"{recorded} -> {executed} executed test(s).\n\n"
                    'Re-run with --allow-decline --reason "<why this is correct>" so the reason '
                    "is recorded in the baseline and reviewed with the diff.",
                    file=sys.stderr,
                )
                return 1
            justification = {"reason": args.reason, "previous_executed_tests": recorded}
    write_suite_baseline(args.suite_baseline, executed, justification)
    return 0


def summarise(cases):
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for case in cases:
        counts[case.outcome] += 1
    counts["total"] = len(cases)
    counts["wall_seconds"] = round(sum(case.time for case in cases), 2)
    return counts


def format_summary(counts, slowest, slow_cases):
    lines = [
        "### Test run",
        "",
        f"{counts['total']} test(s) · {counts['passed']} passed · {counts['failed']} failed · "
        f"{counts['error']} error(s) · {counts['skipped']} skipped · "
        f"{counts['wall_seconds']:.1f}s of accumulated test time (complete serial suite)",
        "",
        f"#### Slowest {len(slowest)} test(s)",
        "",
        "| Test | Seconds |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{case.label}` | {case.time:.2f} |" for case in slowest)
    lines.extend(
        [
            "",
            f"{len(slow_cases)} test(s) over {SLOW_TEST_SECONDS:.0f}s. Durations are published, "
            "not gated: the serial suite stays the correctness source of truth until #21 "
            "proves safe parallel execution.",
        ]
    )
    return "\n".join(lines)


def _sample(cases, render, limit=20):
    """Render at most ``limit`` cases, saying how many were withheld."""
    rendered = [f"    {render(case)}" for case in cases[:limit]]
    if len(cases) > limit:
        rendered.append(f"    ... and {len(cases) - limit} more")
    return rendered


def collect_violations(cases, counts, recorded_tests):
    """Every reason this run may not be certified as a complete, honest pass."""
    violations = []
    if counts["total"] < recorded_tests:
        violations.append(
            f"{counts['total']} test(s) ran, below the reviewed baseline of {recorded_tests}; "
            f"{recorded_tests - counts['total']} test(s) that used to run no longer do. Restore "
            "them, or -- if they were deliberately deleted -- record the smaller suite with "
            '`python scripts/check_test_report.py --write-baseline --allow-decline --reason "..."`.'
        )
    failing = [case for case in cases if case.outcome in {"failed", "error"}]
    if failing:
        violations.append(f"{len(failing)} test(s) did not pass:")
        violations.extend(
            _sample(
                failing,
                lambda case: f"{case.label}: {case.message.splitlines()[0] if case.message else case.outcome}",
            )
        )
    if counts["skipped"] > MAX_SKIPPED_TESTS:
        skipped = [case for case in cases if case.outcome == "skipped"]
        violations.append(
            f"{counts['skipped']} skipped test(s) exceed the allowance of {MAX_SKIPPED_TESTS}; "
            "a skipped test is one that silently did not run:"
        )
        violations.extend(_sample(skipped, lambda case: f"{case.label}: {case.message or 'no reason recorded'}"))
        violations.append(
            "  Fix the condition, delete the test, or raise MAX_SKIPPED_TESTS in "
            "scripts/check_test_report.py with the reason stated in review."
        )
    return violations


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Path to the pytest JUnit XML report.")
    parser.add_argument(
        "--suite-baseline",
        type=Path,
        default=DEFAULT_SUITE_BASELINE,
        help="Path to the reviewed suite-size baseline JSON.",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record the number of tests this run executed as the new reviewed baseline.",
    )
    parser.add_argument(
        "--allow-decline",
        action="store_true",
        help="Permit --write-baseline to record fewer tests than the current baseline.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Justification recorded in the baseline when --allow-decline shrinks the suite.",
    )
    parser.add_argument("--slowest", type=int, default=DEFAULT_SLOWEST, help="How many slow tests to publish.")
    parser.add_argument("--summary-file", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        cases = load_report(args.report)
        counts = summarise(cases)
        if args.write_baseline:
            verify_baseline_write_environment()
            return handle_write(args, counts["total"])
        recorded_tests = read_suite_baseline(args.suite_baseline)["executed_tests"]
    except PolicyError as exc:
        print(f"test report gate failed: {exc}", file=sys.stderr)
        return 2

    ordered = sorted(cases, key=lambda case: case.time, reverse=True)
    slowest = ordered[: max(args.slowest, 0)]
    slow_cases = [case for case in cases if case.time >= SLOW_TEST_SECONDS]

    try:
        write_summary(args.summary_file, format_summary(counts, slowest, slow_cases))
    except PolicyError as exc:
        print(f"test report gate failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"test report: {counts['total']} test(s), {counts['passed']} passed, "
        f"{counts['failed']} failed, {counts['error']} error(s), {counts['skipped']} skipped, "
        f"{counts['wall_seconds']:.1f}s accumulated."
    )
    if slowest:
        print(f"slowest {len(slowest)} test(s):")
        for case in slowest:
            print(f"  {case.time:8.2f}s  {case.label}")

    violations = collect_violations(cases, counts, recorded_tests)
    if violations:
        print("\ntest report gate: the run cannot be certified as complete:\n", file=sys.stderr)
        for entry in violations:
            print(f"  - {entry}" if not entry.startswith("    ") else entry, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
