import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.check_test_report import (
    MAX_SKIPPED_TESTS,
    SLOW_TEST_SECONDS,
    PolicyError,
    load_report,
    main,
    read_suite_baseline,
    summarise,
)


def testcase(name, classname="assets.tests.test_api.TestAssets", time="0.10", outcome=None, message=""):
    """One ``<testcase>`` element in the shape pytest's --junitxml writes."""
    attributes = 'classname="%s" name="%s" time="%s"' % (classname, name, time)
    if outcome is None:
        return "    <testcase %s />" % attributes
    return ('    <testcase %s>\n      <%s message="%s">recorded detail for %s</%s>\n    </testcase>') % (
        attributes,
        outcome,
        message,
        name,
        outcome,
    )


def write_report(root, cases, name="junit.xml"):
    path = Path(root) / name
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<testsuites>\n"
        '  <testsuite name="pytest" errors="0" failures="0" skipped="0" '
        'tests="%d" time="1.234" timestamp="2026-07-25T10:00:00.000000" hostname="runner">\n'
        % len(cases)
        + ("\n".join(cases) + "\n" if cases else "")
        + "  </testsuite>\n"
        "</testsuites>\n",
        encoding="utf-8",
    )
    return path


_AUTO_BASELINE = object()


def run_main(arguments, baseline_tests=_AUTO_BASELINE):
    arguments = list(arguments)
    if "--suite-baseline" not in arguments:
        report = Path(arguments[arguments.index("--report") + 1])
        baseline = report.parent / "suite_baseline.json"
        if baseline_tests is _AUTO_BASELINE:
            try:
                baseline_tests = len(load_report(report))
            except PolicyError:
                baseline_tests = 1
        if baseline_tests is not None:
            baseline.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "canonical_python": "3.12",
                        "executed_tests": baseline_tests,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        arguments.extend(["--suite-baseline", str(baseline)])
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = main(arguments)
    return status, stdout.getvalue(), stderr.getvalue()


class ReportParsingTests(unittest.TestCase):
    """The JUnit XML a pytest run writes is read back faithfully."""

    def test_names_classnames_durations_and_outcomes_are_read_back(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(
                temporary_directory,
                [
                    testcase("test_list", time="0.25"),
                    testcase("test_create", time="6.50", outcome="failure", message="assert 1 == 2"),
                    testcase("test_delete", time="0.75", outcome="error", message="fixture teardown blew up"),
                    testcase("test_ldap", time="0.00", outcome="skipped", message="no python-ldap on Windows"),
                ],
            )

            cases = load_report(path)

            self.assertEqual(
                [(case.label, case.outcome, case.time) for case in cases],
                [
                    ("assets.tests.test_api.TestAssets::test_list", "passed", 0.25),
                    ("assets.tests.test_api.TestAssets::test_create", "failed", 6.5),
                    ("assets.tests.test_api.TestAssets::test_delete", "error", 0.75),
                    ("assets.tests.test_api.TestAssets::test_ldap", "skipped", 0.0),
                ],
            )
            self.assertEqual(cases[3].message, "no python-ldap on Windows")

    def test_a_case_without_a_classname_falls_back_to_its_bare_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_bare", classname="")])

            self.assertEqual(load_report(path)[0].label, "test_bare")

    def test_counts_and_accumulated_time_are_summarised(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(
                temporary_directory,
                [
                    testcase("test_one", time="1.50"),
                    testcase("test_two", time="0.50", outcome="failure", message="boom"),
                    testcase("test_three", time="0.25", outcome="skipped", message="not implemented"),
                ],
            )

            counts = summarise(load_report(path))

            self.assertEqual(counts["total"], 3)
            self.assertEqual(counts["passed"], 1)
            self.assertEqual(counts["failed"], 1)
            self.assertEqual(counts["skipped"], 1)
            self.assertEqual(counts["wall_seconds"], 2.25)


class FailClosedTests(unittest.TestCase):
    """A run the gate cannot see is never a run in which nothing broke."""

    def test_a_missing_report_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "junit.xml"

            status, _, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 2)
            self.assertIn("no test report", stderr)

    def test_an_unparseable_report_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "junit.xml"
            path.write_text("<testsuites><testsuite>truncated", encoding="utf-8")

            status, _, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 2)
            self.assertIn("cannot parse test report", stderr)

    def test_a_report_without_test_cases_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [])

            with self.assertRaisesRegex(PolicyError, "no test cases"):
                load_report(path)

            status, _, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 2)
            self.assertIn("collected nothing", stderr)


class OutcomeGateTests(unittest.TestCase):
    """Recorded failures fail the gate even when a workflow swallowed the exit code."""

    def test_recorded_failures_and_errors_fail_the_gate_and_are_named(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(
                temporary_directory,
                [
                    testcase("test_ok"),
                    testcase("test_broken", outcome="failure", message="assert 1 == 2"),
                    testcase("test_exploded", outcome="error", message="ConnectionError"),
                ],
            )

            status, _, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 1)
            self.assertIn("2 test(s) did not pass", stderr)
            self.assertIn("test_broken", stderr)
            self.assertIn("test_exploded", stderr)
            self.assertIn("assert 1 == 2", stderr)

    def test_a_fully_passing_report_without_skips_passes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_one"), testcase("test_two")])

            status, stdout, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 0, stderr)
            self.assertIn("2 test(s), 2 passed, 0 failed, 0 error(s), 0 skipped", stdout)

    def test_skips_above_the_allowance_fail_the_gate_and_print_the_reason(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            skipped = [
                testcase(f"test_skipped_{index}", outcome="skipped", message=f"reason number {index}")
                for index in range(MAX_SKIPPED_TESTS + 1)
            ]
            path = write_report(temporary_directory, [testcase("test_ok"), *skipped])

            status, _, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 1)
            self.assertIn(f"exceed the allowance of {MAX_SKIPPED_TESTS}", stderr)
            self.assertIn("reason number 0", stderr)
            self.assertIn("MAX_SKIPPED_TESTS", stderr)


class DurationReportingTests(unittest.TestCase):
    """Durations are published for visibility; they never fail the gate."""

    def test_slowest_tests_are_listed_by_descending_duration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(
                temporary_directory,
                [
                    testcase("test_quick", time="0.10"),
                    testcase("test_slowest", time="9.00"),
                    testcase("test_middle", time="3.00"),
                ],
            )

            status, stdout, stderr = run_main(["--report", str(path)])
            listed = [line.split("  ")[-1] for line in stdout.splitlines() if line.startswith("  ")]

            self.assertEqual(status, 0, stderr)
            self.assertEqual(
                listed,
                [
                    "assets.tests.test_api.TestAssets::test_slowest",
                    "assets.tests.test_api.TestAssets::test_middle",
                    "assets.tests.test_api.TestAssets::test_quick",
                ],
            )

    def test_the_slowest_option_limits_how_many_tests_are_published(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = Path(temporary_directory) / "summary.md"
            path = write_report(
                temporary_directory,
                [testcase(f"test_{index}", time=f"{index}.00") for index in range(1, 6)],
            )

            status, stdout, stderr = run_main(["--report", str(path), "--slowest", "2", "--summary-file", str(summary)])
            markdown = summary.read_text(encoding="utf-8")

            self.assertEqual(status, 0, stderr)
            self.assertIn("slowest 2 test(s):", stdout)
            self.assertIn("#### Slowest 2 test(s)", markdown)
            self.assertEqual(markdown.count("| `assets.tests.test_api.TestAssets::"), 2)
            self.assertIn("| `assets.tests.test_api.TestAssets::test_5` | 5.00 |", markdown)
            self.assertIn("| `assets.tests.test_api.TestAssets::test_4` | 4.00 |", markdown)

    def test_a_malformed_duration_attribute_does_not_crash_the_gate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(
                temporary_directory,
                [testcase("test_broken_timing", time="not-a-number"), testcase("test_ok", time="2.00")],
            )

            status, stdout, stderr = run_main(["--report", str(path)])

            self.assertEqual(status, 0, stderr)
            self.assertEqual(load_report(path)[0].time, 0.0)
            self.assertIn("2.0s accumulated", stdout)

    def test_summary_file_reports_the_counts_and_the_slow_test_table(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = Path(temporary_directory) / "summary.md"
            path = write_report(
                temporary_directory,
                [
                    testcase("test_fast", time="0.10"),
                    testcase("test_slow", time=f"{SLOW_TEST_SECONDS + 1:.2f}"),
                ],
            )

            status, _, stderr = run_main(["--report", str(path), "--summary-file", str(summary)])
            markdown = summary.read_text(encoding="utf-8")

            self.assertEqual(status, 0, stderr)
            self.assertIn("### Test run", markdown)
            self.assertIn("2 test(s) · 2 passed · 0 failed · 0 error(s) · 0 skipped", markdown)
            self.assertIn("| Test | Seconds |", markdown)
            self.assertIn(f"| `assets.tests.test_api.TestAssets::test_slow` | {SLOW_TEST_SECONDS + 1:.2f} |", markdown)
            self.assertIn(f"1 test(s) over {SLOW_TEST_SECONDS:.0f}s", markdown)


class SuiteSizeRatchetTests(unittest.TestCase):
    """A green but unexpectedly smaller suite is not certified as complete."""

    def test_a_smaller_suite_fails_against_the_reviewed_baseline(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_only")])

            status, _, stderr = run_main(["--report", str(path)], baseline_tests=2)

            self.assertEqual(status, 1)
            self.assertIn("below the reviewed baseline of 2", stderr)

    def test_suite_growth_is_free(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_one"), testcase("test_two")])

            status, _, stderr = run_main(["--report", str(path)], baseline_tests=1)

            self.assertEqual(status, 0, stderr)

    def test_a_missing_suite_baseline_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_one")])

            status, _, stderr = run_main(["--report", str(path)], baseline_tests=None)

            self.assertEqual(status, 2)
            self.assertIn("no suite baseline", stderr)

    def test_invalid_suite_baselines_fail_closed(self):
        documents = (
            {"schema_version": 2, "canonical_python": "3.12", "executed_tests": 2},
            {"schema_version": 1, "canonical_python": "3.11", "executed_tests": 2},
            {"schema_version": 1, "canonical_python": "3.12", "executed_tests": 0},
            {"schema_version": 1, "canonical_python": "3.12", "executed_tests": True},
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            baseline = Path(temporary_directory) / "suite_baseline.json"
            for document in documents:
                with self.subTest(document=document):
                    baseline.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(PolicyError):
                        read_suite_baseline(baseline)

    def test_writing_a_smaller_suite_requires_a_reason(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_only")])
            with patch("scripts.check_test_report.verify_baseline_write_environment"):
                status, _, stderr = run_main(
                    ["--report", str(path), "--write-baseline"],
                    baseline_tests=2,
                )
                self.assertEqual(status, 1)
                self.assertIn("Refusing to record a smaller suite", stderr)

                status, _, stderr = run_main(
                    [
                        "--report",
                        str(path),
                        "--write-baseline",
                        "--allow-decline",
                        "--reason",
                        "obsolete test removed",
                    ],
                    baseline_tests=2,
                )
                self.assertEqual(status, 0, stderr)

            baseline = json.loads((Path(temporary_directory) / "suite_baseline.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["executed_tests"], 1)
            self.assertEqual(baseline["decline_justification"]["reason"], "obsolete test removed")

    def test_suite_baseline_write_checks_the_canonical_environment(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_report(temporary_directory, [testcase("test_one")])
            with patch("scripts.check_test_report.verify_baseline_write_environment") as guard:
                status, _, stderr = run_main(
                    ["--report", str(path), "--write-baseline"],
                    baseline_tests=1,
                )
            self.assertEqual(status, 0, stderr)
            guard.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
