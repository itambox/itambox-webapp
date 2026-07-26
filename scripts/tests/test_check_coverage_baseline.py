import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.check_coverage_baseline import (
    BASELINE_TOTAL_FIELDS,
    evaluate,
    load_baseline,
    main,
)
from scripts.coverage_policy import (
    CANONICAL_PYTHON,
    DRIFT_PERCENTAGE_POINTS,
    EXCLUDE_ALSO_PATTERNS,
    OMIT_PATTERNS,
    SCHEMA_VERSION,
    TOLERANCE_PERCENTAGE_POINTS,
    compute_policy_fingerprint,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

COVERAGE_VERSION = "7.6.1"
COVERAGE_SERIES = "7.6"
CANONICAL_PYTHON_TEXT = f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}"

# One synthetic run, reused by every case: line 80.00%, branch 75.00%.
MEASURED_SUMMARY = {
    "covered_lines": 80,
    "num_statements": 100,
    "excluded_lines": 5,
    "num_branches": 40,
    "covered_branches": 30,
    "num_partial_branches": 4,
}
MEASURED_TOTALS = {
    "num_statements": 100,
    "covered_lines": 80,
    "line_rate": 80.0,
    "num_branches": 40,
    "covered_branches": 30,
    "branch_rate": 75.0,
    "excluded_lines": 5,
    "measured_files": 1,
}


def toml_array(values):
    """TOML basic-string array; JSON string escaping is a subset of TOML's."""
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def render_pyproject(fail_under=0, omit=OMIT_PATTERNS, exclude_also=EXCLUDE_ALSO_PATTERNS, branch=True):
    return "\n".join(
        [
            "[tool.coverage.run]",
            f"branch = {str(bool(branch)).lower()}",
            "relative_files = true",
            f"omit = {toml_array(omit)}",
            "",
            "[tool.coverage.report]",
            f"fail_under = {fail_under}",
            f"exclude_also = {toml_array(exclude_also)}",
            "",
        ]
    )


class CoverageBaselineGateTests(unittest.TestCase):
    """End-to-end exit codes: 0 compliant, 1 policy violation, 2 unusable input."""

    def setUp(self):
        environment = patch("scripts.check_coverage_baseline.verify_baseline_write_environment")
        self.environment_guard = environment.start()
        self.addCleanup(environment.stop)
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.coverage_json = self.root / "coverage.json"
        self.baseline = self.root / "coverage_baseline.json"
        self.pyproject = self.root / "pyproject.toml"
        self.write_pyproject()
        self.write_report()

    # -- fixtures -----------------------------------------------------------

    def write_pyproject(self, **overrides):
        self.pyproject.write_text(render_pyproject(**overrides), encoding="utf-8")

    def write_report(self, version=COVERAGE_VERSION, branch_coverage=True, **summary_overrides):
        summary = dict(MEASURED_SUMMARY, **summary_overrides)
        document = {
            "meta": {"version": version, "branch_coverage": branch_coverage},
            "files": {"assets/models/asset.py": {"summary": summary}},
            "totals": summary,
        }
        self.coverage_json.write_text(json.dumps(document), encoding="utf-8")

    def write_baseline(self, totals=None, series=COVERAGE_SERIES, **overrides):
        document = {
            "schema_version": SCHEMA_VERSION,
            "canonical_python": CANONICAL_PYTHON_TEXT,
            "coverage_series": series,
            "policy_sha256": compute_policy_fingerprint(series),
            "totals": dict(MEASURED_TOTALS, **(totals or {})),
        }
        document.update(overrides)
        self.baseline.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return document

    # -- invocation ---------------------------------------------------------

    def run_main(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        arguments = [
            "--coverage-json",
            str(self.coverage_json),
            "--baseline",
            str(self.baseline),
            "--pyproject",
            str(self.pyproject),
            *extra,
        ]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(arguments)
        return status, stdout.getvalue() + stderr.getvalue()

    # -- compliant runs -----------------------------------------------------

    def test_matching_baseline_passes(self):
        self.write_baseline()

        status, output = self.run_main()

        self.assertEqual(status, 0, output)
        self.assertIn("line 80.00% (80/100)", output)
        self.assertIn("branch 75.00% (30/40)", output)
        self.assertIn("1 measured file(s)", output)

    def test_drop_inside_the_tolerance_passes(self):
        self.write_baseline({"line_rate": 80.0 + TOLERANCE_PERCENTAGE_POINTS / 2})

        status, output = self.run_main()

        self.assertEqual(status, 0, output)
        self.assertNotIn("fell", output)

    def test_fewer_excluded_lines_than_recorded_passes(self):
        self.write_baseline({"excluded_lines": MEASURED_TOTALS["excluded_lines"] + 3})

        status, output = self.run_main()

        self.assertEqual(status, 0, output)

    # -- regressions --------------------------------------------------------

    def test_line_rate_regression_beyond_tolerance_fails(self):
        self.write_baseline({"line_rate": 85.0})

        status, output = self.run_main()

        self.assertEqual(status, 1)
        self.assertIn("line coverage fell to 80.00% from the recorded 85.00%", output)
        self.assertIn("regressed against the reviewed baseline", output)
        self.assertIn("--allow-decline", output)

    def test_branch_rate_is_gated_independently_of_the_line_rate(self):
        # Line rate is exactly on baseline; only the branch rate moved.
        self.write_baseline({"branch_rate": 85.0})

        status, output = self.run_main()

        self.assertEqual(status, 1)
        self.assertIn("branch coverage fell to 75.00% from the recorded 85.00%", output)
        self.assertNotIn("line coverage fell", output)

    def test_rate_above_the_drift_allowance_is_stale(self):
        self.write_baseline({"line_rate": 80.0 - DRIFT_PERCENTAGE_POINTS - 0.5})

        status, output = self.run_main()

        self.assertEqual(status, 1)
        self.assertIn("stale", output)
        self.assertIn("line coverage rose to 80.00%", output)
        self.assertIn("--write-baseline", output)

    def test_growth_in_excluded_lines_fails_with_unchanged_rates(self):
        self.write_baseline({"excluded_lines": 2})

        status, output = self.run_main()

        self.assertEqual(status, 1)
        self.assertIn("excluded lines grew to 5 from the recorded 2", output)
        self.assertNotIn("coverage fell", output)

    def test_a_smaller_measured_denominator_is_review_required(self):
        cases = (
            ("measured_files", 2, "measured file(s) fell"),
            ("num_statements", 101, "measured statement(s) fell"),
            ("num_branches", 41, "measured branch(es) fell"),
        )
        for field, recorded_value, message in cases:
            with self.subTest(field=field):
                self.write_baseline({field: recorded_value})

                status, output = self.run_main()

                self.assertEqual(status, 1)
                self.assertIn(message, output)

    def test_evaluate_reports_regressions_and_staleness_together(self):
        current = dict(MEASURED_TOTALS, branch_rate=60.0, excluded_lines=9)
        recorded = dict(MEASURED_TOTALS, line_rate=70.0, branch_rate=75.0, excluded_lines=5)

        regressions, stale_notes = evaluate(current, recorded)

        self.assertEqual(len(regressions), 2)
        self.assertEqual(len(stale_notes), 1)
        self.assertTrue(any("branch coverage fell" in entry for entry in regressions))
        self.assertTrue(any("excluded lines grew" in entry for entry in regressions))

    # -- unusable input -----------------------------------------------------

    def test_missing_baseline_reports_a_bootstrap_hint(self):
        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("no coverage baseline at", output)
        self.assertIn("--write-baseline", output)

    def test_line_only_report_is_rejected(self):
        self.write_baseline()
        self.write_report(branch_coverage=False)

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("without branch measurement", output)

    def test_missing_report_is_rejected(self):
        self.write_baseline()
        self.coverage_json.unlink()

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("no coverage report at", output)

    def test_weakened_measurement_policy_is_rejected(self):
        self.write_baseline()
        self.write_pyproject(omit=OMIT_PATTERNS + ("*/services.py",))

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("measurement policy mismatch", output)

    def test_fail_under_above_the_measured_rate_is_rejected(self):
        self.write_baseline()
        self.write_pyproject(fail_under=95)

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("fail_under is 95", output)
        # coverage.py's own metric folds lines and branches: (80+30)/(100+40).
        self.assertIn("combined rate of 78.57%", output)

    def test_fail_under_at_or_below_the_measured_rate_is_accepted(self):
        self.write_baseline()
        self.write_pyproject(fail_under=45)

        status, output = self.run_main()

        self.assertEqual(status, 0, output)

    def test_fail_under_is_compared_against_the_combined_rate_not_the_line_rate(self):
        # 79 sits below the 80.00% line rate but above the 78.57% combined rate
        # coverage.py itself applies the floor to, so the floor is unenforceable.
        self.write_baseline()
        self.write_pyproject(fail_under=79)

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("fail_under is 79", output)

    def test_baseline_bindings_are_enforced(self):
        cases = {
            "wrong policy_sha256": ({"policy_sha256": "0" * 64}, "policy_sha256 does not match"),
            "wrong schema_version": ({"schema_version": SCHEMA_VERSION + 1}, "baseline schema"),
            "wrong canonical_python": ({"canonical_python": "3.11"}, "canonical_python must be"),
            "unknown field": ({"totals_note": "hand-edited"}, "invalid top-level fields"),
        }
        for label, (overrides, message) in cases.items():
            with self.subTest(case=label):
                self.write_baseline(**overrides)

                status, output = self.run_main()

                self.assertEqual(status, 2)
                self.assertIn(message, output)

    def test_a_baseline_from_another_coverage_series_is_rejected(self):
        self.write_baseline(series="7.5")

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("recorded with coverage.py 7.5.x", output)
        self.assertIn("regenerate the baseline", output)

    def test_malformed_baseline_totals_are_rejected(self):
        cases = {
            "missing field": {field: None for field in ("line_rate",)},
            "negative value": {"covered_lines": -1},
            "boolean value": {"measured_files": True},
            "non-numeric value": {"branch_rate": "75.00"},
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                totals = dict(MEASURED_TOTALS)
                for field, value in changes.items():
                    if value is None:
                        del totals[field]
                    else:
                        totals[field] = value
                self.write_baseline()
                document = json.loads(self.baseline.read_text(encoding="utf-8"))
                document["totals"] = totals
                self.baseline.write_text(json.dumps(document), encoding="utf-8")

                status, output = self.run_main()

                self.assertEqual(status, 2)
                self.assertIn("baseline total", output)

    def test_unreadable_baseline_json_is_rejected(self):
        self.baseline.write_text("{not json,", encoding="utf-8")

        status, output = self.run_main()

        self.assertEqual(status, 2)
        self.assertIn("cannot read coverage baseline", output)

    # -- recording a baseline ----------------------------------------------

    def test_write_baseline_records_the_measured_run_and_then_passes(self):
        status, output = self.run_main("--write-baseline")

        self.assertEqual(status, 0, output)
        self.environment_guard.assert_called_once_with()
        self.assertIn("Wrote coverage baseline", output)

        recorded = load_baseline(self.baseline, compute_policy_fingerprint(COVERAGE_SERIES), COVERAGE_SERIES)
        self.assertEqual(recorded["schema_version"], SCHEMA_VERSION)
        self.assertEqual(recorded["canonical_python"], CANONICAL_PYTHON_TEXT)
        self.assertEqual(recorded["coverage_series"], COVERAGE_SERIES)
        self.assertEqual(set(recorded["totals"]), set(BASELINE_TOTAL_FIELDS))
        self.assertEqual(recorded["totals"], MEASURED_TOTALS)
        self.assertNotIn("decline_justification", recorded)
        self.assertTrue(self.baseline.read_text(encoding="utf-8").endswith("\n"))

        status, output = self.run_main()
        self.assertEqual(status, 0, output)

    def test_write_baseline_refuses_an_unjustified_decline(self):
        self.write_baseline({"line_rate": 90.0, "branch_rate": 85.0})

        for label, extra in (
            ("no flags", ()),
            ("flag without reason", ("--allow-decline",)),
            ("reason without flag", ("--reason", "a module was deleted")),
        ):
            with self.subTest(case=label):
                status, output = self.run_main("--write-baseline", *extra)

                self.assertEqual(status, 1)
                self.assertIn("Refusing to record a coverage decline", output)
                self.assertIn("line coverage 90.00% -> 80.00%", output)
                self.assertIn("branch coverage 85.00% -> 75.00%", output)
                # The recorded baseline is untouched by a refused write.
                document = json.loads(self.baseline.read_text(encoding="utf-8"))
                self.assertEqual(document["totals"]["line_rate"], 90.0)

    def test_write_baseline_refuses_unjustified_growth_in_excluded_lines(self):
        self.write_baseline({"excluded_lines": 1})

        status, output = self.run_main("--write-baseline")

        self.assertEqual(status, 1)
        self.assertIn("excluded lines 1 -> 5", output)

    def test_write_baseline_records_a_justified_decline(self):
        self.write_baseline({"line_rate": 90.0, "branch_rate": 85.0, "excluded_lines": 1})

        status, output = self.run_main(
            "--write-baseline",
            "--allow-decline",
            "--reason",
            "a well-covered module was deleted",
        )

        self.assertEqual(status, 0, output)
        recorded = load_baseline(self.baseline, compute_policy_fingerprint(COVERAGE_SERIES), COVERAGE_SERIES)
        self.assertEqual(recorded["totals"], MEASURED_TOTALS)
        self.assertEqual(
            recorded["decline_justification"],
            {
                "reason": "a well-covered module was deleted",
                "previous_line_rate": 90.0,
                "previous_branch_rate": 85.0,
                "previous_excluded_lines": 1,
                "previous_measured_files": 1,
                "previous_num_statements": 100,
                "previous_num_branches": 40,
            },
        )

        status, output = self.run_main()
        self.assertEqual(status, 0, output)

    def test_superseded_policy_does_not_bypass_decline_justification(self):
        self.write_baseline({"line_rate": 90.0}, series="7.5")

        status, output = self.run_main("--write-baseline")

        self.assertEqual(status, 1, output)
        self.assertIn("Refusing to record a coverage decline", output)

        status, output = self.run_main(
            "--write-baseline",
            "--allow-decline",
            "--reason",
            "coverage.py upgrade changed measurement semantics",
        )
        self.assertEqual(status, 0, output)
        recorded = load_baseline(self.baseline, compute_policy_fingerprint(COVERAGE_SERIES), COVERAGE_SERIES)
        self.assertEqual(recorded["coverage_series"], COVERAGE_SERIES)
        self.assertEqual(recorded["totals"]["line_rate"], 80.0)

    def test_schema_or_python_binding_change_does_not_bypass_decline_justification(self):
        for label, overrides in (
            ("schema", {"schema_version": SCHEMA_VERSION + 1}),
            ("python", {"canonical_python": "3.11"}),
        ):
            with self.subTest(binding=label):
                self.write_baseline({"line_rate": 90.0}, **overrides)

                status, output = self.run_main("--write-baseline")

                self.assertEqual(status, 1, output)
                self.assertIn("Refusing to record a coverage decline", output)
                self.assertEqual(
                    json.loads(self.baseline.read_text(encoding="utf-8"))["totals"]["line_rate"],
                    90.0,
                )

    def test_write_baseline_refuses_an_unusable_report(self):
        self.write_report(branch_coverage=False)

        status, output = self.run_main("--write-baseline")

        self.assertEqual(status, 2)
        self.assertFalse(self.baseline.exists())

    # -- job summary --------------------------------------------------------

    def test_summary_file_appends_the_measured_and_baseline_rates(self):
        self.write_baseline({"line_rate": 79.95, "branch_rate": 74.9, "excluded_lines": 6})
        summary_file = self.root / "summary.md"
        summary_file.write_text("previous block\n", encoding="utf-8")

        status, output = self.run_main("--summary-file", str(summary_file))

        self.assertEqual(status, 0, output)
        summary = summary_file.read_text(encoding="utf-8")
        self.assertTrue(summary.startswith("previous block\n"))
        self.assertIn("### Global coverage", summary)
        self.assertIn("| Line coverage | 80.00% | 79.95% | +0.05 |", summary)
        self.assertIn("| Branch coverage | 75.00% | 74.90% | +0.10 |", summary)
        self.assertIn("| Excluded lines | 5 | 6 | -1 |", summary)
        self.assertIn("Statements 80/100", summary)
        self.assertIn("branches 30/40", summary)

    def test_summary_is_written_even_when_the_gate_fails(self):
        self.write_baseline({"line_rate": 95.0})
        summary_file = self.root / "summary.md"

        status, output = self.run_main("--summary-file", str(summary_file))

        self.assertEqual(status, 1)
        self.assertIn("| Line coverage | 80.00% | 95.00% | -15.00 |", summary_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
