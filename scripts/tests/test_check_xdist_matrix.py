"""Tests for the xdist validation matrix gate.

The gate answers the two questions pytest's exit code cannot: every parallel
iteration must contain the identical set of node IDs (a worker crash that
shrinks the report otherwise reads as a green, smaller run), and the
serial-only lane must be disjoint from the parallel partition (a test in both
lanes would race in one run and be excluded in the next).
"""

import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from scripts.check_xdist_matrix import MatrixError, collect_violations, load_report, main


def write_junit(path, cases, tests_attr=None):
    """Write a minimal pytest-style JUnit report. ``cases`` maps label -> outcome."""
    suite = ElementTree.Element("testsuite", {"name": "pytest"})
    if tests_attr is not None:
        suite.set("tests", str(tests_attr))
    for label, outcome in cases.items():
        classname, _, name = label.rpartition("::")
        case = ElementTree.SubElement(suite, "testcase", {"classname": classname, "name": name, "time": "0.01"})
        if outcome != "passed":
            # pytest's JUnit writer emits <failure>, not <failed>; the helper
            # must write the same tags the gate reads.
            tag = "failure" if outcome == "failed" else outcome
            ElementTree.SubElement(case, tag, {"message": f"boom: {label}"})
    ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def write_cases(path, *labels):
    return write_junit(path, {label: "passed" for label in labels})


def run_gate(xdist_files, serial_files=None):
    argv = []
    for path in xdist_files:
        argv += ["--xdist", str(path)]
    for path in serial_files or []:
        argv += ["--serial", str(path)]
    return main(argv)


class LoadReportTests(unittest.TestCase):
    def test_missing_report_fails_closed(self):
        with self.assertRaises(MatrixError):
            load_report("no/such/file.xml")

    def test_unparseable_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.xml"
            path.write_text("this is not xml", encoding="utf-8")
            with self.assertRaises(MatrixError):
                load_report(path)

    def test_empty_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.xml"
            suite = ElementTree.Element("testsuite", {"name": "pytest"})
            ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaises(MatrixError):
                load_report(path)

    def test_skipped_case_is_recorded_as_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.xml"
            write_junit(path, {"a.tests.test_x::TestA::test_skip": "skipped"})
            cases = load_report(path)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].outcome, "skipped")


class MatrixParityTests(unittest.TestCase):
    def test_identical_iterations_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            common = {
                "a.tests.test_x::TestA::test_one": "passed",
                "a.tests.test_x::TestA::test_two[0]": "passed",
                "a.tests.test_x::TestA::test_two[1]": "passed",
            }
            first = write_junit(Path(tmp) / "xdist-1.xml", common)
            second = write_junit(Path(tmp) / "xdist-2.xml", common)
            self.assertEqual(run_gate([first, second]), 0)

    def test_missing_node_id_in_one_iteration_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = {
                "a.tests.test_x::TestA::test_one": "passed",
                "a.tests.test_x::TestA::test_two": "passed",
            }
            first = write_junit(Path(tmp) / "xdist-1.xml", full)
            second = write_junit(Path(tmp) / "xdist-2.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            self.assertEqual(run_gate([first, second]), 1)

    def test_extra_node_id_in_one_iteration_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            second = write_junit(
                Path(tmp) / "xdist-2.xml",
                {"a.tests.test_x::TestA::test_one": "passed", "a.tests.test_x::TestA::test_two": "passed"},
            )
            self.assertEqual(run_gate([first, second]), 1)

    def test_failed_case_fails_the_matrix_even_with_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "failed"})
            second = write_junit(Path(tmp) / "xdist-2.xml", {"a.tests.test_x::TestA::test_one": "failed"})
            self.assertEqual(run_gate([first, second]), 1)

    def test_skipped_case_fails_the_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "skipped"})
            self.assertEqual(run_gate([path]), 1)


class LaneDisjointnessTests(unittest.TestCase):
    def test_disjoint_lanes_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdist = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            serial = write_junit(
                Path(tmp) / "serial.xml",
                {
                    "a.tests.test_serial::TestSerial::test_seed": "passed",
                    "a.tests.test_serial::TestSerial::test_race": "passed",
                },
            )
            self.assertEqual(run_gate([xdist], [serial]), 0)

    def test_overlapping_lanes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdist = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            serial = write_junit(Path(tmp) / "serial.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            self.assertEqual(run_gate([xdist], [serial]), 1)


class GateRobustnessTests(unittest.TestCase):
    def test_missing_report_returns_usage_exit_code(self):
        self.assertEqual(run_gate(["no/such/file.xml"]), 2)

    def test_single_iteration_without_serial_lane_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_junit(Path(tmp) / "xdist-1.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            self.assertEqual(run_gate([path]), 0)

    def test_collect_violations_names_missing_and_added_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = {"a.tests.test_x::TestA::test_one": "passed", "a.tests.test_x::TestA::test_two": "passed"}
            first = write_junit(Path(tmp) / "xdist-1.xml", full)
            second = write_junit(Path(tmp) / "xdist-2.xml", {"a.tests.test_x::TestA::test_one": "passed"})
            violations = collect_violations(
                {
                    str(first): load_report(first),
                    str(second): load_report(second),
                },
                {str(first), str(second)},
                set(),
            )
            self.assertTrue(any("missing 1 node ID" in entry for entry in violations))


if __name__ == "__main__":
    unittest.main()
