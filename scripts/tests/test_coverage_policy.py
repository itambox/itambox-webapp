import json
import tempfile
import unittest
from pathlib import Path

from scripts.coverage_policy import (
    COVERAGE_ROOT,
    EXCLUDE_ALSO_PATTERNS,
    OMIT_PATTERNS,
    PYPROJECT_PATH,
    PolicyError,
    branch_rate,
    combined_rate,
    compute_policy_fingerprint,
    coverage_series,
    exemption_reason,
    is_omitted,
    line_rate,
    load_coverage_report,
    load_measurement_config,
    normalise_path,
    rate,
    to_coverage_path,
    to_repo_path,
    verify_baseline_write_environment,
    verify_measurement_policy,
    write_summary,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

SUMMARY_FIELDS = {
    "covered_lines": 8,
    "num_statements": 10,
    "excluded_lines": 1,
    "num_branches": 4,
    "covered_branches": 3,
    "num_partial_branches": 1,
}


def toml_array(values):
    """TOML basic-string array; JSON string escaping is a subset of TOML's."""
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def render_pyproject(
    branch=True,
    relative_files=True,
    omit=OMIT_PATTERNS,
    exclude_also=EXCLUDE_ALSO_PATTERNS,
    fail_under=0,
):
    return "\n".join(
        [
            "[tool.coverage.run]",
            f"branch = {str(bool(branch)).lower()}",
            f"relative_files = {str(bool(relative_files)).lower()}",
            f"omit = {toml_array(omit)}",
            "",
            "[tool.coverage.report]",
            f"fail_under = {fail_under}",
            f"exclude_also = {toml_array(exclude_also)}",
            "",
        ]
    )


def coverage_document(version="7.6.1", branch_coverage=True, files=None, totals=None, **meta_overrides):
    meta = {"version": version, "branch_coverage": branch_coverage}
    meta.update(meta_overrides)
    document = {
        "meta": meta,
        "files": {"assets/models/asset.py": {"summary": dict(SUMMARY_FIELDS)}} if files is None else files,
        "totals": dict(SUMMARY_FIELDS) if totals is None else totals,
    }
    return document


class PathTranslationTests(unittest.TestCase):
    """Measured paths and repository paths are one conversion apart."""

    def test_windows_separators_and_dot_prefixes_are_normalised(self):
        self.assertEqual(normalise_path("itambox\\assets\\models.py"), "itambox/assets/models.py")
        self.assertEqual(normalise_path("./itambox/assets/models.py"), "itambox/assets/models.py")
        self.assertEqual(normalise_path(".\\itambox\\assets\\models.py"), "itambox/assets/models.py")
        self.assertEqual(normalise_path("././assets/models.py"), "assets/models.py")
        self.assertEqual(normalise_path(Path("assets") / "models.py"), "assets/models.py")

    def test_coverage_and_repository_paths_round_trip(self):
        self.assertEqual(to_repo_path("assets/models.py"), f"{COVERAGE_ROOT}/assets/models.py")
        self.assertEqual(to_coverage_path(f"{COVERAGE_ROOT}/assets/models.py"), "assets/models.py")
        self.assertEqual(to_coverage_path(to_repo_path("assets/models.py")), "assets/models.py")
        repo_path = f"{COVERAGE_ROOT}/core/tasks.py"
        self.assertEqual(to_repo_path(to_coverage_path(repo_path)), repo_path)

    def test_backslash_repository_paths_are_translated(self):
        self.assertEqual(to_coverage_path(f"{COVERAGE_ROOT}\\assets\\models.py"), "assets/models.py")
        self.assertEqual(to_repo_path("assets\\models.py"), f"{COVERAGE_ROOT}/assets/models.py")

    def test_paths_outside_the_measured_tree_have_no_coverage_path(self):
        self.assertIsNone(to_coverage_path("scripts/check_coverage_baseline.py"))
        self.assertIsNone(to_coverage_path("docs/development/test-coverage-policy.md"))
        self.assertIsNone(to_coverage_path(COVERAGE_ROOT))
        self.assertIsNone(to_coverage_path(f"{COVERAGE_ROOT}-vendor/thing.py"))


class OmissionTests(unittest.TestCase):
    """What the declared policy does and does not measure."""

    def test_generated_and_test_code_is_omitted(self):
        for path in (
            "assets/migrations/0001_initial.py",
            "migrations/0001_initial.py",
            "assets/tests/test_api.py",
            "tests/e2e/conftest.py",
            "assets/tests.py",
            "core/conftest.py",
            "manage.py",
            "core/wsgi.py",
            "core/asgi.py",
            "itambox/wsgi.py",
            "itambox/asgi.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_omitted(path))

    def test_top_level_conftest_and_tests_module_are_omitted(self):
        # coverage.py treats the leading ``*/`` as optional, so the run itself
        # never measures itambox/conftest.py; the policy must agree.
        self.assertTrue(is_omitted("conftest.py"))
        self.assertTrue(is_omitted("tests.py"))
        self.assertTrue(is_omitted("tests/e2e/test_login.py"))

    def test_application_modules_are_measured(self):
        for path in (
            "assets/models/asset.py",
            "assets/views/request_views.py",
            "core/managers.py",
            "extras/testing_helpers.py",
            "assets/contests.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_omitted(path))

    def test_omission_is_separator_agnostic(self):
        self.assertTrue(is_omitted("assets\\migrations\\0001_initial.py"))
        self.assertFalse(is_omitted("assets\\models\\asset.py"))


class ExemptionTests(unittest.TestCase):
    """Structurally unmeasurable paths carry a documented reason."""

    def test_repository_tooling_is_exempt_with_a_reason(self):
        reason = exemption_reason("scripts/check_coverage_baseline.py")
        self.assertIsNotNone(reason)
        self.assertIn("scripts/tests/", reason)
        self.assertEqual(exemption_reason("scripts\\tests\\test_coverage_policy.py"), reason)

    def test_e2e_and_frontend_paths_are_exempt(self):
        self.assertIn("Playwright", exemption_reason("itambox/tests/e2e/test_login.spec.py"))
        self.assertIn("frontend", exemption_reason("itambox/static/src/index.ts"))

    def test_application_code_is_never_exempt(self):
        self.assertIsNone(exemption_reason("itambox/assets/models.py"))
        self.assertIsNone(exemption_reason("itambox/core/managers.py"))


class RateTests(unittest.TestCase):
    """Percentages are rounded once, and "nothing to cover" is not a failure."""

    def test_zero_total_is_fully_covered(self):
        self.assertEqual(rate(0, 0), 100.0)

    def test_rates_are_rounded_to_two_decimal_places(self):
        self.assertEqual(rate(1, 3), 33.33)
        self.assertEqual(rate(2, 3), 66.67)
        self.assertEqual(rate(1, 8), 12.5)
        self.assertEqual(rate(19_113, 19_998), 95.57)

    def test_full_and_empty_coverage(self):
        self.assertEqual(rate(10, 10), 100.0)
        self.assertEqual(rate(0, 10), 0.0)

    def test_totals_drive_the_line_and_branch_rates(self):
        totals = dict(SUMMARY_FIELDS)
        self.assertEqual(line_rate(totals), 80.0)
        self.assertEqual(branch_rate(totals), 75.0)

    def test_a_run_without_branches_is_not_a_branch_failure(self):
        totals = dict(SUMMARY_FIELDS, num_branches=0, covered_branches=0)
        self.assertEqual(branch_rate(totals), 100.0)

    def test_combined_rate_folds_branches_into_coverage_own_metric(self):
        totals = dict(SUMMARY_FIELDS)
        # (8 + 3) / (10 + 4) -- between the 80.00% line and 75.00% branch rates.
        self.assertEqual(combined_rate(totals), 78.57)
        self.assertEqual(combined_rate(dict(totals, num_branches=0, covered_branches=0)), 80.0)


class MeasurementConfigTests(unittest.TestCase):
    """The gate refuses to run under a weakened measurement configuration."""

    def load(self, text):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "pyproject.toml"
            path.write_text(text, encoding="utf-8")
            return load_measurement_config(path)

    def test_configuration_is_read_from_pyproject(self):
        config = self.load(render_pyproject(fail_under=45))

        self.assertTrue(config.branch)
        self.assertTrue(config.relative_files)
        self.assertEqual(config.omit, OMIT_PATTERNS)
        self.assertEqual(config.exclude_also, EXCLUDE_ALSO_PATTERNS)
        self.assertEqual(config.fail_under, 45)
        verify_measurement_policy(config)

    def test_missing_or_unparsable_pyproject_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(PolicyError):
                load_measurement_config(Path(temporary_directory) / "absent.toml")
        with self.assertRaises(PolicyError):
            self.load("[tool.coverage.run\nbranch = true\n")

    def test_absent_coverage_section_is_not_a_usable_policy(self):
        config = self.load('[project]\nname = "itambox"\n')

        self.assertFalse(config.branch)
        self.assertEqual(config.omit, ())
        with self.assertRaises(PolicyError):
            verify_measurement_policy(config)

    def test_disabling_branch_measurement_is_rejected(self):
        with self.assertRaisesRegex(PolicyError, "branch must be true"):
            verify_measurement_policy(self.load(render_pyproject(branch=False)))

    def test_disabling_relative_files_is_rejected(self):
        with self.assertRaisesRegex(PolicyError, "relative_files must be true"):
            verify_measurement_policy(self.load(render_pyproject(relative_files=False)))

    def test_an_extra_omit_entry_is_rejected(self):
        weakened = OMIT_PATTERNS + ("*/services.py",)
        with self.assertRaisesRegex(PolicyError, "omit does not match"):
            verify_measurement_policy(self.load(render_pyproject(omit=weakened)))

    def test_a_removed_or_reordered_omit_entry_is_rejected(self):
        for label, omit in (
            ("removed", OMIT_PATTERNS[1:]),
            ("reordered", tuple(reversed(OMIT_PATTERNS))),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(PolicyError, "omit does not match"):
                    verify_measurement_policy(self.load(render_pyproject(omit=omit)))

    def test_a_changed_line_exclusion_is_rejected(self):
        weakened = EXCLUDE_ALSO_PATTERNS + ("except ImportError:",)
        with self.assertRaisesRegex(PolicyError, "exclude_also does not match"):
            verify_measurement_policy(self.load(render_pyproject(exclude_also=weakened)))

    def test_every_weakening_is_reported_together(self):
        text = render_pyproject(branch=False, relative_files=False, omit=(), exclude_also=())
        with self.assertRaises(PolicyError) as caught:
            verify_measurement_policy(self.load(text))

        message = str(caught.exception)
        for fragment in ("branch must be true", "relative_files must be true", "omit does not match", "exclude_also"):
            self.assertIn(fragment, message)

    def test_repository_pyproject_matches_the_declared_policy(self):
        # Drift guard: scripts/coverage_policy.py and pyproject.toml are one
        # policy expressed twice, and this is the assertion that keeps them one.
        config = load_measurement_config(PYPROJECT_PATH)

        verify_measurement_policy(config)
        self.assertEqual(config.omit, OMIT_PATTERNS)
        self.assertEqual(config.exclude_also, EXCLUDE_ALSO_PATTERNS)
        self.assertEqual(PYPROJECT_PATH, REPOSITORY_ROOT / "pyproject.toml")


class CoverageReportLoadingTests(unittest.TestCase):
    """Every unusable report shape fails closed rather than reporting a number."""

    def load(self, payload):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "coverage.json"
            text = payload if isinstance(payload, str) else json.dumps(payload)
            path.write_text(text, encoding="utf-8")
            return load_coverage_report(path)

    def test_missing_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(PolicyError, "no coverage report at"):
                load_coverage_report(Path(temporary_directory) / "coverage.json")

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(PolicyError, "cannot read coverage report"):
            self.load("{not json,")

    def test_a_non_object_report_is_rejected(self):
        with self.assertRaisesRegex(PolicyError, "not a JSON object"):
            self.load([])

    def test_a_report_without_meta_is_rejected(self):
        for label, document in (
            ("absent", {"files": {}, "totals": {}}),
            ("not an object", coverage_document()),
        ):
            with self.subTest(case=label):
                if label == "not an object":
                    document["meta"] = "7.6.1"
                with self.assertRaisesRegex(PolicyError, "has no meta section"):
                    self.load(document)

    def test_line_only_report_is_rejected(self):
        for label, document in (
            ("branch coverage false", coverage_document(branch_coverage=False)),
            ("branch coverage absent", coverage_document()),
            ("branch coverage truthy but not true", coverage_document(branch_coverage=1)),
        ):
            with self.subTest(case=label):
                if label == "branch coverage absent":
                    del document["meta"]["branch_coverage"]
                with self.assertRaisesRegex(PolicyError, "without branch measurement"):
                    self.load(document)

    def test_a_report_without_a_recorded_version_is_rejected(self):
        for label, version in (("absent", None), ("empty", ""), ("not a string", 7.6)):
            with self.subTest(case=label):
                document = coverage_document()
                if version is None:
                    del document["meta"]["version"]
                else:
                    document["meta"]["version"] = version
                with self.assertRaisesRegex(PolicyError, "does not record the coverage.py version"):
                    self.load(document)

    def test_a_report_that_measured_no_files_is_rejected(self):
        for label, files in (("empty", {}), ("absent", None), ("not an object", [])):
            with self.subTest(case=label):
                document = coverage_document(files=files if files is not None else {})
                if label == "absent":
                    del document["files"]
                with self.assertRaisesRegex(PolicyError, "measured no files"):
                    self.load(document)

    def test_a_report_without_totals_is_rejected(self):
        document = coverage_document()
        del document["totals"]
        with self.assertRaisesRegex(PolicyError, "has no totals section"):
            self.load(document)

    def test_totals_missing_a_summary_field_are_rejected(self):
        for field in sorted(SUMMARY_FIELDS):
            with self.subTest(field=field):
                totals = {key: value for key, value in SUMMARY_FIELDS.items() if key != field}
                with self.assertRaisesRegex(PolicyError, field):
                    self.load(coverage_document(totals=totals))

    def test_a_report_with_zero_statements_is_rejected(self):
        totals = dict(SUMMARY_FIELDS, num_statements=0, covered_lines=0)
        with self.assertRaisesRegex(PolicyError, "recorded zero statements"):
            self.load(coverage_document(totals=totals))

    def test_a_file_entry_without_a_usable_summary_is_rejected(self):
        cases = {
            "entry is not an object": {"assets/models.py": "measured"},
            "entry has no summary": {"assets/models.py": {"executed_lines": [1]}},
            "summary is incomplete": {
                "assets/models.py": {
                    "summary": {key: value for key, value in SUMMARY_FIELDS.items() if key != "num_branches"}
                }
            },
        }
        for label, files in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(PolicyError, "assets/models.py"):
                    self.load(coverage_document(files=files))

    def test_a_valid_report_is_normalised(self):
        files = {
            ".\\assets\\models\\asset.py": {"summary": dict(SUMMARY_FIELDS)},
            "core/managers.py": {"summary": dict(SUMMARY_FIELDS)},
        }
        report = self.load(coverage_document(version="7.15.2", files=files))

        self.assertEqual(report.coverage_version, "7.15.2")
        self.assertEqual(sorted(report.files), ["assets/models/asset.py", "core/managers.py"])
        self.assertEqual(report.totals["num_statements"], SUMMARY_FIELDS["num_statements"])
        self.assertEqual(line_rate(report.totals), 80.0)


class FingerprintTests(unittest.TestCase):
    """A baseline is bound to the policy and the tool that produced it."""

    def test_fingerprint_is_stable_for_the_same_inputs(self):
        first = compute_policy_fingerprint("7.6")

        self.assertEqual(first, compute_policy_fingerprint("7.6"))
        self.assertEqual(len(first), 64)
        self.assertEqual(set(first) - set("0123456789abcdef"), set())

    def test_fingerprint_changes_with_the_coverage_series(self):
        self.assertNotEqual(compute_policy_fingerprint("7.6"), compute_policy_fingerprint("7.7"))
        self.assertNotEqual(compute_policy_fingerprint("7.6"), compute_policy_fingerprint("8.0"))

    def test_series_is_the_major_minor_pair(self):
        self.assertEqual(coverage_series("7.6.1"), "7.6")
        self.assertEqual(coverage_series("7.15.2"), "7.15")
        self.assertEqual(coverage_series("8.0"), "8.0")
        self.assertEqual(coverage_series("7.6.1a1"), "7.6")

    def test_a_malformed_version_is_rejected(self):
        for version in ("7", "", "seven"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(PolicyError, "unrecognised coverage.py version"):
                    coverage_series(version)


class BaselineWriteEnvironmentTests(unittest.TestCase):
    def test_canonical_linux_python_312_is_accepted(self):
        self.assertIsNone(verify_baseline_write_environment((3, 12), "linux"))

    def test_noncanonical_python_or_platform_is_rejected(self):
        for version, platform in (((3, 11), "linux"), ((3, 12), "win32")):
            with self.subTest(version=version, platform=platform):
                with self.assertRaisesRegex(PolicyError, "canonical measurement environment"):
                    verify_baseline_write_environment(version, platform)


class SummaryWritingTests(unittest.TestCase):
    """Job summaries are appended, never truncated, and optional."""

    def test_no_summary_file_is_a_no_op(self):
        self.assertIsNone(write_summary(None, "### ignored"))

    def test_blocks_are_appended_with_a_trailing_blank_line(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "summary.md"
            path.write_text("previous\n", encoding="utf-8")

            write_summary(path, "### first\n\n\n")
            write_summary(path, "### second")

            self.assertEqual(path.read_text(encoding="utf-8"), "previous\n### first\n\n### second\n\n")

    def test_an_unwritable_summary_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "summary.md"
            directory.mkdir()
            with self.assertRaisesRegex(PolicyError, "cannot write summary"):
                write_summary(directory, "### block")


if __name__ == "__main__":
    unittest.main()
