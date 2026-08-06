#!/usr/bin/env python
"""Combined gate for the xdist validation matrix: lane parity and disjointness.

Issue #21 acceptance requires the suite to run repeatedly under
``pytest -n auto`` without intermittent failures before CI is switched to
parallel execution. A green exit code from pytest alone is too weak for that
claim: a worker that crashes after collecting can leave a JUnit report with
zero failures and a quietly smaller test set, and a test that runs in BOTH
lanes (the parallel partition and the serial-only lane) invalidates the
partitioning itself -- it would be racing in one run and excluded in the next.

This gate answers the two questions pytest's exit code cannot:

* **Lane parity.** Every iteration of the parallel lane must have produced the
  exact same set of node IDs. One missing or duplicated test in any iteration
  fails the whole matrix, because a suite whose membership changes between
  repetitions is by definition not stable.
* **Lane disjointness.** The serial-only lane (``-m serial_only``) must not
  contain any node ID that also ran in the parallel lane. A test in both lanes
  would be measured twice and sequenced inconsistently between runs.

Like the other report gates it is deliberately redundant with pytest's own
exit code: this is the check that still fires if a future workflow edit
swallows an exit code behind ``|| true`` or ``continue-on-error``. It also
fails closed on a missing, unparseable, or empty report -- a run that
collected nothing must never be certified as a run in which nothing broke.

Node IDs are the ``classname::name`` labels pytest's JUnit writer emits, the
same spelling ``check_test_report.py`` uses for its duration table, so the two
gates agree on what a "test" is (including parametrized cases and subtests).
"""

import argparse
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path


class MatrixError(Exception):
    """A malformed or missing report; a usage problem, not a suite finding."""


class TestCase:
    __slots__ = ("label", "outcome", "message")

    def __init__(self, label, outcome, message=""):
        self.label = label
        self.outcome = outcome  # passed | failed | error | skipped
        self.message = message


def load_report(report_path):
    """Parse one pytest JUnit XML report, failing closed on anything unusable."""
    path = Path(report_path)
    try:
        tree = ElementTree.parse(path)
    except FileNotFoundError as exc:
        raise MatrixError(f"no test report at {path}") from exc
    except (OSError, ElementTree.ParseError) as exc:
        raise MatrixError(f"cannot parse test report {path}: {exc}") from exc

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
        classname = element.get("classname") or ""
        name = element.get("name") or "<unnamed>"
        cases.append(TestCase(f"{classname}::{name}" if classname else name, outcome, message))
    if not cases:
        raise MatrixError(f"test report {path} contains no test cases")
    return cases


def summarize(reports):
    """Per-report outcome counts, keyed by report path."""
    summary = {}
    for path, cases in reports.items():
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for case in cases:
            counts[case.outcome] += 1
        summary[path] = counts
    return summary


def _outcome_violations(reports):
    """Find failed, errored, or skipped cases in every report."""
    violations = []
    for path, cases in reports.items():
        for case in cases:
            if case.outcome == "failed":
                violations.append(f"{path}: test {case.label!r} failed: {case.message[:200]}")
            elif case.outcome == "error":
                violations.append(f"{path}: test {case.label!r} errored: {case.message[:200]}")
            elif case.outcome == "skipped":
                violations.append(f"{path}: test {case.label!r} was skipped (the suite must run complete)")
    return violations


def _lane_sets(reports, lane_paths):
    """Node-ID set per report path, restricted to one lane."""
    return {str(path): {case.label for case in cases} for path, cases in reports.items() if path in lane_paths}


def _parity_violations(xdist_sets):
    """Find iterations whose node-ID membership differs from the first one."""
    first_path, first_set = next(iter(xdist_sets.items()))
    violations = []
    for path, node_ids in xdist_sets.items():
        if node_ids == first_set:
            continue
        missing = sorted(first_set - node_ids)[:5]
        added = sorted(node_ids - first_set)[:5]
        detail = []
        if missing:
            detail.append(f"missing {len(first_set - node_ids)} node ID(s), e.g. {missing}")
        if added:
            detail.append(f"added {len(node_ids - first_set)} node ID(s), e.g. {added}")
        violations.append(f"{path}: iteration membership differs from {first_path}; " + "; ".join(detail))
    return violations, first_set


def _overlap_violations(serial_sets, first_set):
    """Find serial-only node IDs that also ran in the parallel lane."""
    serial_ids = {label for node_ids in serial_sets.values() for label in node_ids}
    overlap = sorted(first_set & serial_ids)[:5]
    if not overlap:
        return []
    return [f"serial-only lane overlaps the parallel lane in {len(first_set & serial_ids)} node ID(s), e.g. {overlap}"]


def collect_violations(reports, xdist_paths, serial_paths):
    """Return a list of human-readable findings; empty means the matrix passes."""
    violations = _outcome_violations(reports)
    parity_violations, first_set = _parity_violations(_lane_sets(reports, xdist_paths))
    violations.extend(parity_violations)
    serial_sets = _lane_sets(reports, serial_paths)
    if serial_sets:
        violations.extend(_overlap_violations(serial_sets, first_set))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xdist",
        action="append",
        required=True,
        metavar="REPORT",
        help="JUnit report of one parallel-lane iteration (repeatable)",
    )
    parser.add_argument(
        "--serial",
        action="append",
        default=[],
        metavar="REPORT",
        help="JUnit report of the serial-only lane (repeatable)",
    )
    args = parser.parse_args(argv)

    reports = {}
    try:
        for path in args.xdist + args.serial:
            reports[str(path)] = load_report(path)
    except MatrixError as exc:
        print(f"xdist matrix gate failed: {exc}", file=sys.stderr)
        return 2

    xdist_paths = {str(path) for path in args.xdist}
    serial_paths = {str(path) for path in args.serial}

    violations = collect_violations(reports, xdist_paths, serial_paths)
    if violations:
        print("xdist matrix gate: the matrix cannot be certified:\n", file=sys.stderr)
        for entry in violations:
            print(f"  - {entry}", file=sys.stderr)
        return 1

    counts = summarize(reports)
    first = counts[args.xdist[0]]
    print(
        f"xdist matrix: {len(args.xdist)} iteration(s), {first['passed']} passed "
        f"({first['failed']} failed, {first['error']} error(s), {first['skipped']} skipped) per iteration, "
        f"identical node IDs across all iterations",
        end="",
    )
    if serial_paths:
        serial_counts = summarize({path: reports[path] for path in serial_paths})
        serial_total = sum(c["passed"] + c["failed"] + c["error"] + c["skipped"] for c in serial_counts.values())
        print(f", serial-only lane: {serial_total} test(s), disjoint from the parallel lane", end="")
    print(".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
