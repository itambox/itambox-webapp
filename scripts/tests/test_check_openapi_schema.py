import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_openapi_schema import (
    BASELINE_SCHEMA_VERSION,
    GenerationResult,
    PolicyError,
    build_header,
    check_tracked_schema,
    compare_identities,
    generate_twice,
    load_baseline,
    parse_spectacular_stderr,
    render_baseline,
    render_diagnostics_report,
    update_baseline,
    verify_write_environment,
)
from scripts.openapi_identity import DiagnosticIdentity

HEADER = {
    "schema_version": BASELINE_SCHEMA_VERSION,
    "canonical_python": "3.12",
    "canonical_platform": "linux",
    "django_version": "5.2.4",
    "djangorestframework_version": "3.17.1",
    "drf_spectacular_version": "0.29.0",
    "pyyaml_version": "6.0.2",
    "python_hash_seed": "0",
    "settings_sha256": "1" * 64,
    "policy_sha256": "2" * 64,
}
WARNING = DiagnosticIdentity("warning", "itambox/assets/api/serializers.py", "AssetSerializer", "warning")
ERROR = DiagnosticIdentity("error", "<unknown>", "SCIMView", "error")


def write_lf(path, text):
    path.write_bytes(text.encode("utf-8"))


class OpenApiBaselineTests(unittest.TestCase):
    def test_baseline_round_trip_is_sorted_lf_and_count_free(self):
        rendered = render_baseline([WARNING, ERROR], HEADER)

        self.assertTrue(rendered.endswith("\n"))
        self.assertNotIn("\r", rendered)
        document = json.loads(rendered)
        self.assertEqual(document["diagnostics"], [ERROR.as_dict(), WARNING.as_dict()])
        self.assertNotIn("count", rendered)

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            path.write_text(rendered, encoding="utf-8", newline="")
            self.assertEqual(load_baseline(path, HEADER), {ERROR, WARNING})

    def test_noncanonical_baseline_order_is_rejected(self):
        document = dict(HEADER, diagnostics=[WARNING.as_dict(), ERROR.as_dict()])
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            write_lf(path, json.dumps(document) + "\n")

            with self.assertRaisesRegex(PolicyError, "canonical sorted form"):
                load_baseline(path, HEADER)

    def test_missing_malformed_and_wrong_bound_baselines_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            with self.assertRaisesRegex(PolicyError, "no OpenAPI diagnostics baseline"):
                load_baseline(path, HEADER)

            write_lf(path, "{not json")
            with self.assertRaisesRegex(PolicyError, "cannot read"):
                load_baseline(path, HEADER)

            write_lf(path, render_baseline([WARNING], dict(HEADER, policy_sha256="3" * 64)))
            with self.assertRaisesRegex(PolicyError, "policy_sha256"):
                load_baseline(path, HEADER)

    def test_new_and_stale_identities_are_reported_together(self):
        new, stale = compare_identities({WARNING}, {ERROR})

        self.assertEqual(new, {WARNING})
        self.assertEqual(stale, {ERROR})

    def test_occurrence_counts_are_not_part_of_identity_comparison(self):
        new, stale = compare_identities([WARNING, WARNING], [WARNING])

        self.assertEqual(new, set())
        self.assertEqual(stale, set())

    def test_baseline_update_refuses_new_debt_but_accepts_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            write_lf(path, render_baseline([WARNING], HEADER))

            with patch("scripts.check_openapi_schema.verify_write_environment"):
                with self.assertRaisesRegex(PolicyError, "refusing to grandfather"):
                    update_baseline(path, {WARNING, ERROR}, HEADER)
            self.assertEqual(load_baseline(path, HEADER), {WARNING})

            with patch("scripts.check_openapi_schema.verify_write_environment"):
                update_baseline(path, set(), HEADER)
            self.assertEqual(load_baseline(path, HEADER), set())

    def test_bootstrap_write_is_allowed_only_when_baseline_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"

            with patch("scripts.check_openapi_schema.verify_write_environment"):
                update_baseline(path, {WARNING}, HEADER)

            self.assertEqual(load_baseline(path, HEADER), {WARNING})

    def test_write_environment_is_linux_python_312_only(self):
        verify_write_environment(version_info=(3, 12), platform_name="linux")
        for version, platform_name, expected in (
            ((3, 11), "linux", "Python 3.12"),
            ((3, 12), "win32", "linux"),
        ):
            with self.subTest(version=version, platform=platform_name):
                with self.assertRaisesRegex(PolicyError, expected):
                    verify_write_environment(version_info=version, platform_name=platform_name)

    def test_update_calls_environment_guard_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            with patch("scripts.check_openapi_schema.verify_write_environment") as guard:
                update_baseline(path, {WARNING}, HEADER)

            guard.assert_called_once_with()


class OpenApiGenerationPolicyTests(unittest.TestCase):
    def result(self, schema=b"openapi: 3.0.3\n", diagnostics=None):
        return GenerationResult(schema=schema, diagnostics=diagnostics or {WARNING: 2})

    def test_two_clean_generations_must_match_bytes_and_diagnostics(self):
        calls = []

        def generate():
            calls.append(None)
            return self.result()

        result = generate_twice(generate)

        self.assertEqual(result.schema, b"openapi: 3.0.3\n")
        self.assertEqual(len(calls), 2)

    def test_second_generation_with_different_schema_fails(self):
        results = iter((self.result(), self.result(schema=b"openapi: 3.1.0\n")))

        with self.assertRaisesRegex(PolicyError, "byte-for-byte"):
            generate_twice(lambda: next(results))

    def test_second_generation_with_different_diagnostic_occurrences_fails(self):
        results = iter((self.result(), self.result(diagnostics={WARNING: 3})))

        with self.assertRaisesRegex(PolicyError, "diagnostics"):
            generate_twice(lambda: next(results))

    def test_tracked_schema_must_be_exact_lf_bytes_without_bom(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "schema.yaml"
            path.write_bytes(b"openapi: 3.0.3\n")
            check_tracked_schema(b"openapi: 3.0.3\n", path)

            path.write_bytes(b"openapi: 3.0.3\r\n")
            with self.assertRaisesRegex(PolicyError, "LF"):
                check_tracked_schema(b"openapi: 3.0.3\n", path)

            path.write_bytes(b"\xef\xbb\xbfopenapi: 3.0.3\n")
            with self.assertRaisesRegex(PolicyError, "BOM"):
                check_tracked_schema(b"openapi: 3.0.3\n", path)

    def test_tracked_schema_mismatch_is_a_named_policy_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "schema.yaml"
            path.write_bytes(b"openapi: 3.0.3\n")

            with self.assertRaisesRegex(PolicyError, "snapshot differs"):
                check_tracked_schema(b"openapi: 3.1.0\n", path)

    def test_header_fingerprint_binds_settings_plugins_versions_and_hash_seed(self):
        versions = {
            "django": "5.2.4",
            "djangorestframework": "3.17.1",
            "drf_spectacular": "0.29.0",
            "pyyaml": "6.0.2",
        }
        first = build_header(versions, {"TITLE": "ITAMbox", "SORT_OPERATIONS": True}, [], "0")
        reordered = build_header(versions, {"SORT_OPERATIONS": True, "TITLE": "ITAMbox"}, [], "0")
        changed = build_header(versions, {"TITLE": "ITAMbox", "SORT_OPERATIONS": True}, ["plugin"], "0")

        self.assertEqual(first, reordered)
        self.assertNotEqual(first["settings_sha256"], changed["settings_sha256"])
        self.assertNotEqual(first["policy_sha256"], changed["policy_sha256"])

    def test_diagnostics_report_records_counts_without_putting_them_in_identity(self):
        report = json.loads(render_diagnostics_report({WARNING: 2, ERROR: 1}, HEADER))

        self.assertEqual(report["summary"], {"warnings": 2, "errors": 1, "unique_warnings": 1, "unique_errors": 1})
        self.assertEqual(report["diagnostics"][0]["identity"], ERROR.as_dict())
        self.assertEqual(report["diagnostics"][0]["occurrences"], 1)
        self.assertEqual(report["diagnostics"][1]["occurrences"], 2)

    def test_spectacular_stderr_is_parsed_strictly_and_summary_verified(self):
        warning = (
            r"C:\work\itambox\itambox\assets\api\serializers.py:141: "
            "Warning [AssetSerializer]: warning"
        )
        stderr = "\n".join(
            (
                warning,
                "Error [SCIMView]: error",
                "",
                "Schema generation summary:",
                "Warnings: 2 (1 unique)",
                "Errors:   1 (1 unique)",
                "",
            )
        )

        diagnostics, occurrences = parse_spectacular_stderr(stderr, Path("C:/work/itambox"))

        normalized_warning = DiagnosticIdentity(
            "warning", "itambox/assets/api/serializers.py", "AssetSerializer", "warning"
        )
        self.assertEqual(diagnostics, {normalized_warning: 1, ERROR: 1})
        self.assertEqual(occurrences, {"warnings": 2, "errors": 1})

    def test_spectacular_stderr_rejects_unknown_output_and_summary_mismatch(self):
        valid_diagnostic = "Warning [AssetSerializer]: warning"
        cases = (
            (
                "unexpected log line\n"
                f"{valid_diagnostic}\n\n"
                "Schema generation summary:\nWarnings: 1 (1 unique)\nErrors: 0 (0 unique)\n",
                "unrecognized stderr",
            ),
            (
                f"{valid_diagnostic}\n\nSchema generation summary:\nWarnings: 2 (2 unique)\nErrors: 0 (0 unique)\n",
                "summary does not match",
            ),
            (valid_diagnostic + "\n", "missing schema generation summary"),
        )
        for stderr, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(PolicyError, expected):
                    parse_spectacular_stderr(stderr, Path("C:/work/itambox"))


if __name__ == "__main__":
    unittest.main()
