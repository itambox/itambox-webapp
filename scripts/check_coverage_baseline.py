#!/usr/bin/env python
"""Fail-closed ratchet for ITAMbox's global line and branch coverage.

CI runs the complete serial suite -- pytest-django creates and migrates its own
test database from scratch under ``--create-db`` -- and writes
``itambox/coverage.json``. This gate holds that report against the reviewed
baseline in ``scripts/coverage_baseline.json``:

* A rate more than ``TOLERANCE_PERCENTAGE_POINTS`` below the recorded one is a
  regression and fails the build. Smaller movements are jitter and pass.
* A rate more than ``DRIFT_PERCENTAGE_POINTS`` *above* the recorded one is also
  a failure, asking for the baseline to be regenerated. Unrecorded improvement
  is headroom, and headroom is what lets a later change ship untested code
  without any gate noticing. An improvement inside that allowance is not a
  failure; it is simply not yet recorded.
* Growth in excluded lines fails too. ``# pragma: no cover`` and the configured
  line exclusions are invisible to every rate, so an unreviewed exclusion is a
  coverage decline the percentages would not show.
* A fall in how much was measured -- files, statements or branches -- fails as
  well. A rate is a ratio: measuring less code raises it without a single test
  being written, so a shrinking denominator is review-required rather than
  something a branch may do to itself.

Lowering the baseline is possible but never silent: it requires
``--write-baseline --allow-decline --reason "..."``, and the justification is
recorded in the baseline file next to the rates it replaces. That requirement
holds even when the policy fingerprint or the coverage.py series moved in the
same change -- that is precisely the change a decline could otherwise hide in.
A baseline may only be written on canonical Python and Linux, because a
baseline recorded anywhere else records numbers CI cannot reproduce.

The gate refuses to run at all (exit code 2) on an unusable report -- missing,
line-only, empty, or produced under a measurement configuration that no longer
matches the declared policy. Exit code 1 means a real policy violation.
"""

import argparse
import json
import sys
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, not the repository root.
# The shared policy module has to import identically here and from the unittest
# suites, which address it as ``scripts.coverage_policy``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.coverage_policy import (  # noqa: E402  -- deliberate, see the bootstrap above
    BASELINE_PATH,
    CANONICAL_PYTHON,
    COVERAGE_ROOT,
    DRIFT_PERCENTAGE_POINTS,
    PYPROJECT_PATH,
    RATCHETED_COVERAGE_SIZE_FIELDS,
    REPO_ROOT,
    SCHEMA_VERSION,
    TOLERANCE_PERCENTAGE_POINTS,
    PolicyError,
    branch_rate,
    combined_rate,
    compute_policy_fingerprint,
    coverage_series,
    line_rate,
    load_coverage_report,
    load_measurement_config,
    verify_baseline_write_environment,
    verify_measurement_policy,
    write_summary,
)

DEFAULT_COVERAGE_JSON = REPO_ROOT / COVERAGE_ROOT / "coverage.json"

BASELINE_TOTAL_FIELDS = (
    "num_statements",
    "covered_lines",
    "line_rate",
    "num_branches",
    "covered_branches",
    "branch_rate",
    "excluded_lines",
    "measured_files",
)

# Size fields, ratcheted separately from the rates. A rate is a ratio and says
# nothing about how much was measured: deleting a poorly covered module, losing
# a package to a collection error, or an `omit` that quietly matched more than
# it used to all *raise* both rates while measuring less code. Growth is free;
# a decrease is review-required and must be recorded deliberately.
BASELINE_SIZE_FIELDS = RATCHETED_COVERAGE_SIZE_FIELDS


def current_totals(report):
    """Reduce a coverage report to the fields the baseline records."""
    totals = report.totals
    return {
        "num_statements": totals["num_statements"],
        "covered_lines": totals["covered_lines"],
        "line_rate": line_rate(totals),
        "num_branches": totals["num_branches"],
        "covered_branches": totals["covered_branches"],
        "branch_rate": branch_rate(totals),
        "excluded_lines": totals["excluded_lines"],
        "measured_files": len(report.files),
    }


def _validate_totals(totals):
    if not isinstance(totals, dict) or set(totals) != set(BASELINE_TOTAL_FIELDS):
        raise PolicyError(f"baseline totals must have exactly the fields: {', '.join(BASELINE_TOTAL_FIELDS)}")
    for field, value in totals.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise PolicyError(f"baseline total {field!r} is not a non-negative number")


def read_baseline(baseline_path):
    """Read and structurally validate a baseline, without checking its bindings.

    Split from the binding checks on purpose. A baseline bound to a superseded
    policy or coverage.py series cannot be *compared* against a new run, but its
    recorded numbers are still the numbers this repository last reviewed. They
    have to stay visible to ``--write-baseline``, or bumping coverage.py in the
    same change would let an arbitrary decline through without the explicit
    justification the gate exists to demand.
    """
    path = Path(baseline_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(
            f"no coverage baseline at {path}; bootstrap one from a clean run with "
            "`python scripts/check_coverage_baseline.py --write-baseline`"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read coverage baseline {path}: {exc}") from exc

    required = {"schema_version", "canonical_python", "coverage_series", "policy_sha256", "totals"}
    optional = {"decline_justification"}
    if not isinstance(raw, dict) or not required <= set(raw) or not set(raw) <= (required | optional):
        raise PolicyError("coverage baseline has invalid top-level fields")
    _validate_totals(raw["totals"])
    return raw


def verify_baseline_bindings(raw, expected_fingerprint, expected_series):
    """Refuse a baseline that is not bound to this exact policy and tool series."""
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected coverage baseline schema {SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if raw["coverage_series"] != expected_series:
        raise PolicyError(
            f"baseline was recorded with coverage.py {raw['coverage_series']}.x but this report "
            f"was produced by {expected_series}.x; measurement semantics differ between series, "
            "so regenerate the baseline in the same change that upgrades coverage.py"
        )
    if raw["policy_sha256"] != expected_fingerprint:
        raise PolicyError(
            "baseline policy_sha256 does not match the effective coverage policy; "
            "regenerate the baseline in the reviewed change that altered "
            "scripts/coverage_policy.py"
        )


def load_baseline(baseline_path, expected_fingerprint, expected_series):
    """Load the recorded baseline, refusing anything not bound to this policy."""
    raw = read_baseline(baseline_path)
    verify_baseline_bindings(raw, expected_fingerprint, expected_series)
    return raw


def write_baseline(baseline_path, totals, fingerprint, series, justification=None):
    data = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "coverage_series": series,
        "policy_sha256": fingerprint,
        "totals": {field: totals[field] for field in BASELINE_TOTAL_FIELDS},
    }
    if justification is not None:
        data["decline_justification"] = justification
    Path(baseline_path).write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Wrote coverage baseline to {baseline_path}: "
        f"line {totals['line_rate']:.2f}% ({totals['covered_lines']}/{totals['num_statements']}), "
        f"branch {totals['branch_rate']:.2f}% ({totals['covered_branches']}/{totals['num_branches']}), "
        f"{totals['excluded_lines']} excluded line(s) across {totals['measured_files']} measured file(s)."
    )


def evaluate(current, recorded):
    """Compare a measured run against the baseline.

    Returns (regressions, stale_notes): both empty means the run is compliant.
    """
    regressions = []
    stale_notes = []
    for field, label in (("line_rate", "line"), ("branch_rate", "branch")):
        measured = current[field]
        baseline_value = recorded[field]
        if measured < baseline_value - TOLERANCE_PERCENTAGE_POINTS:
            regressions.append(
                f"{label} coverage fell to {measured:.2f}% from the recorded {baseline_value:.2f}% "
                f"(tolerance {TOLERANCE_PERCENTAGE_POINTS:.2f} pp)"
            )
        elif measured > baseline_value + DRIFT_PERCENTAGE_POINTS:
            stale_notes.append(
                f"{label} coverage rose to {measured:.2f}% from the recorded {baseline_value:.2f}% "
                f"(drift allowance {DRIFT_PERCENTAGE_POINTS:.2f} pp)"
            )
    if current["excluded_lines"] > recorded["excluded_lines"]:
        regressions.append(
            f"excluded lines grew to {current['excluded_lines']} from the recorded "
            f"{recorded['excluded_lines']}; excluded lines are invisible to both rates, so each "
            "new exclusion must be reviewed rather than absorbed"
        )
    regressions.extend(shrink_notes(current, recorded))
    return regressions, stale_notes


def shrink_notes(current, recorded):
    """Every measured-size field that fell below the recorded one."""
    return [
        f"{label} fell to {current[field]} from the recorded {recorded[field]}; measuring less "
        "code raises both rates without a test being written, so a genuine deletion is recorded "
        "deliberately rather than absorbed"
        for field, label in BASELINE_SIZE_FIELDS
        if current[field] < recorded[field]
    ]


def report_mismatches(regressions, stale_notes):
    """Explain a failed comparison and the one reviewed way out of it."""
    if regressions:
        print("global coverage regressed against the reviewed baseline:\n")
        for entry in regressions:
            print(f"  - {entry}")
        print(
            "\nAdd tests for the changed code, or -- if the decline is genuinely correct "
            "(for example a well-covered module was deleted) -- record it explicitly with\n"
            '  python scripts/check_coverage_baseline.py --write-baseline --allow-decline --reason "..."'
        )
    if stale_notes:
        if regressions:
            print()
        print("the coverage baseline is stale -- record the improvement so it cannot become headroom:\n")
        for entry in stale_notes:
            print(f"  - {entry}")
        print("\n  python scripts/check_coverage_baseline.py --write-baseline")


def format_summary(current, recorded):
    """Markdown block for the CI job summary."""
    lines = [
        "### Global coverage",
        "",
        "| Metric | Measured | Baseline | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    rows = (
        ("Line coverage", "line_rate", "%"),
        ("Branch coverage", "branch_rate", "%"),
        ("Excluded lines", "excluded_lines", ""),
        ("Measured files", "measured_files", ""),
        ("Measured statements", "num_statements", ""),
        ("Measured branches", "num_branches", ""),
    )
    for label, field, unit in rows:
        measured = current[field]
        baseline_value = recorded[field] if recorded else None
        if baseline_value is None:
            lines.append(f"| {label} | {measured}{unit} | – | – |")
            continue
        delta = measured - baseline_value
        if unit == "%":
            lines.append(f"| {label} | {measured:.2f}{unit} | {baseline_value:.2f}{unit} | {delta:+.2f} |")
        else:
            lines.append(f"| {label} | {measured} | {baseline_value} | {delta:+d} |")
    lines.extend(
        [
            "",
            f"Statements {current['covered_lines']}/{current['num_statements']} · "
            f"branches {current['covered_branches']}/{current['num_branches']} · "
            "measured from the complete serial suite against a test database pytest-django "
            "created and migrated from scratch.",
        ]
    )
    return "\n".join(lines)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=DEFAULT_COVERAGE_JSON,
        help="Path to the coverage.py JSON report produced by the test run.",
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="Path to the coverage baseline JSON.")
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT_PATH, help="Path to pyproject.toml.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record the measured rates as the new reviewed baseline.",
    )
    parser.add_argument(
        "--allow-decline",
        action="store_true",
        help="Permit --write-baseline to record a rate below the current baseline.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Justification recorded in the baseline when --allow-decline lowers a rate.",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=None,
        help="Append a markdown summary here (e.g. $GITHUB_STEP_SUMMARY).",
    )
    return parser.parse_args(argv)


def collect_declines(current, recorded):
    """Everything about a proposed baseline that is worse than the recorded one."""
    declines = [
        f"{label} coverage {recorded[field]:.2f}% -> {current[field]:.2f}%"
        for field, label in (("line_rate", "line"), ("branch_rate", "branch"))
        if current[field] < recorded[field] - TOLERANCE_PERCENTAGE_POINTS
    ]
    if current["excluded_lines"] > recorded["excluded_lines"]:
        declines.append(f"excluded lines {recorded['excluded_lines']} -> {current['excluded_lines']}")
    declines.extend(
        f"{label} {recorded[field]} -> {current[field]}"
        for field, label in BASELINE_SIZE_FIELDS
        if current[field] < recorded[field]
    )
    return declines


def _existing_baseline(baseline_path, fingerprint, series):
    """The recorded baseline to compare a write against, and whether it still binds.

    A baseline whose bindings no longer hold is still compared. Regenerating it
    is exactly the change most likely to hide a decline -- the policy or the
    coverage.py version moved in the same commit -- so the justification
    requirement has to survive that, not be skipped by it.
    """
    if not Path(baseline_path).exists():
        return None, None
    try:
        existing = read_baseline(baseline_path)
    except PolicyError:
        # Structurally unreadable: there is no recorded number to compare
        # against, so a reviewed write replaces it wholesale.
        return None, None
    try:
        verify_baseline_bindings(existing, fingerprint, series)
    except PolicyError as exc:
        return existing, str(exc)
    return existing, None


def _handle_write(args, current, fingerprint, series):
    """Write a new baseline, refusing an unjustified decline."""
    justification = None
    existing, unbound_reason = _existing_baseline(args.baseline, fingerprint, series)
    if existing is not None:
        recorded = existing["totals"]
        declines = collect_declines(current, recorded)
        if declines:
            if unbound_reason is not None:
                print(f"note: the recorded baseline no longer binds to this run ({unbound_reason})", file=sys.stderr)
            if not args.allow_decline or not args.reason:
                print(
                    "Refusing to record a coverage decline without an explicit justification:\n  - "
                    + "\n  - ".join(declines)
                    + '\n\nRe-run with --allow-decline --reason "<why this is correct>" so the '
                    "reason is recorded in the baseline and reviewed with the diff.",
                    file=sys.stderr,
                )
                return 1
            justification = {
                "reason": args.reason,
                "previous_line_rate": recorded["line_rate"],
                "previous_branch_rate": recorded["branch_rate"],
                "previous_excluded_lines": recorded["excluded_lines"],
                "previous_measured_files": recorded["measured_files"],
                "previous_num_statements": recorded["num_statements"],
                "previous_num_branches": recorded["num_branches"],
            }
    write_baseline(args.baseline, current, fingerprint, series, justification)
    return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        config = load_measurement_config(args.pyproject)
        verify_measurement_policy(config)
        report = load_coverage_report(args.coverage_json)
        series = coverage_series(report.coverage_version)
        fingerprint = compute_policy_fingerprint(series)
        current = current_totals(report)
        measured_combined = combined_rate(report.totals)
        if config.fail_under > measured_combined + TOLERANCE_PERCENTAGE_POINTS:
            raise PolicyError(
                f"[tool.coverage.report] fail_under is {config.fail_under}, above the measured "
                f"combined rate of {measured_combined:.2f}% (coverage.py's own metric, lines and "
                "branches together); the coarse floor must stay at or below what the suite "
                "actually achieves, or every run fails the floor and the floor enforces nothing"
            )
        if args.write_baseline:
            verify_baseline_write_environment()
            return _handle_write(args, current, fingerprint, series)
        baseline = load_baseline(args.baseline, fingerprint, series)
    except PolicyError as exc:
        print(f"coverage baseline gate failed: {exc}", file=sys.stderr)
        return 2

    recorded = baseline["totals"]
    regressions, stale_notes = evaluate(current, recorded)
    try:
        write_summary(args.summary_file, format_summary(current, recorded))
    except PolicyError as exc:
        print(f"coverage baseline gate failed: {exc}", file=sys.stderr)
        return 2

    if regressions or stale_notes:
        report_mismatches(regressions, stale_notes)
        return 1

    print(
        f"global coverage: line {current['line_rate']:.2f}% "
        f"({current['covered_lines']}/{current['num_statements']}), "
        f"branch {current['branch_rate']:.2f}% "
        f"({current['covered_branches']}/{current['num_branches']}) "
        f"across {current['measured_files']} measured file(s); "
        f"baseline line {recorded['line_rate']:.2f}%, branch {recorded['branch_rate']:.2f}%."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
