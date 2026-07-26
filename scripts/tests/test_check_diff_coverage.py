import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_diff_coverage import (
    evaluate_file,
    format_line_ranges,
    main,
    parse_diff,
)
from scripts.coverage_policy import (
    DIFF_COVERAGE_TARGET,
    EXCLUDE_ALSO_PATTERNS,
    OMIT_PATTERNS,
    PolicyError,
    load_coverage_report,
)

COVERAGE_VERSION = "7.6.1"


def toml_list(values):
    """Render a TOML array of basic strings (JSON escaping is TOML-compatible)."""
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def write_pyproject(root):
    """A pyproject whose measurement policy matches the declared one exactly."""
    path = Path(root) / "pyproject.toml"
    path.write_text(
        "[tool.coverage.run]\n"
        "branch = true\n"
        "relative_files = true\n"
        f"omit = {toml_list(OMIT_PATTERNS)}\n"
        "\n"
        "[tool.coverage.report]\n"
        "fail_under = 45\n"
        f"exclude_also = {toml_list(EXCLUDE_ALSO_PATTERNS)}\n",
        encoding="utf-8",
    )
    return path


def coverage_entry(executed=(), missing=(), excluded=(), missing_branches=()):
    """One coverage.py JSON file entry in the shape the differential gate reads."""
    return {
        "executed_lines": sorted(executed),
        "missing_lines": sorted(missing),
        "excluded_lines": sorted(excluded),
        "missing_branches": [list(pair) for pair in missing_branches],
        "summary": {
            "covered_lines": len(executed),
            "num_statements": len(executed) + len(missing),
            "excluded_lines": len(excluded),
            "num_branches": 2 * len(missing_branches),
            "covered_branches": len(missing_branches),
            "num_partial_branches": len(missing_branches),
        },
    }


def coverage_document(files, branch_coverage=True):
    covered = sum(len(entry.get("executed_lines", ())) for entry in files.values())
    statements = covered + sum(len(entry.get("missing_lines", ())) for entry in files.values())
    return {
        "meta": {"version": COVERAGE_VERSION, "branch_coverage": branch_coverage, "show_contexts": False},
        "files": files,
        "totals": {
            "covered_lines": covered,
            "num_statements": max(statements, 1),
            "excluded_lines": 0,
            "num_branches": 0,
            "covered_branches": 0,
            "num_partial_branches": 0,
        },
    }


def load_report(files):
    """Build a validated CoverageReport from synthetic file entries."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "coverage.json"
        path.write_text(json.dumps(coverage_document(files)), encoding="utf-8")
        return load_coverage_report(path)


def parse_changed_lines(diff_text):
    """The attributable changed lines of a diff, for the many cases that only assert those."""
    return parse_diff(diff_text).changed


def diff_for(path, hunks):
    """A ``git diff --unified=0`` style diff adding ``count`` lines at ``start``."""
    lines = [f"diff --git a/{path} b/{path}", "index 1111111..2222222 100644", f"--- a/{path}", f"+++ b/{path}"]
    for start, count in hunks:
        lines.append(f"@@ -{max(start - 1, 0)},0 +{start},{count} @@")
        lines.extend(f"+    line_{number} = {number}" for number in range(start, start + count))
    return "\n".join(lines) + "\n"


class DiffParsingTests(unittest.TestCase):
    """Only lines present in the post-change file are attributable to the author."""

    def test_multiple_files_multiple_hunks_and_count_free_headers_are_parsed(self):
        diff_text = (
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ -3 +3 @@\n"
            "-    old = 1\n"
            "+    new = 1\n"
            "@@ -10,0 +11,3 @@ class Asset:\n"
            "+    one = 1\n"
            "+    two = 2\n"
            "+    three = 3\n"
            "diff --git a/itambox/inventory/services.py b/itambox/inventory/services.py\n"
            "--- a/itambox/inventory/services.py\n"
            "+++ b/itambox/inventory/services.py\n"
            "@@ -40,0 +41,2 @@\n"
            "+    first = 1\n"
            "+    second = 2\n"
        )

        self.assertEqual(
            parse_changed_lines(diff_text),
            {
                "itambox/assets/models.py": {3, 11, 12, 13},
                "itambox/inventory/services.py": {41, 42},
            },
        )

    def test_deleted_files_and_pure_deletion_hunks_contribute_nothing(self):
        diff_text = (
            "diff --git a/itambox/assets/legacy.py b/itambox/assets/legacy.py\n"
            "deleted file mode 100644\n"
            "--- a/itambox/assets/legacy.py\n"
            "+++ /dev/null\n"
            "@@ -1,4 +0,0 @@\n"
            "-alpha = 1\n"
            "-beta = 2\n"
            "-gamma = 3\n"
            "-delta = 4\n"
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ -5,3 +4,0 @@\n"
            "-removed_one = 1\n"
            "-removed_two = 2\n"
            "-removed_three = 3\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {})

    def test_paths_without_a_b_prefix_are_recorded_verbatim(self):
        """`git diff --no-prefix` output, still anchored by its `diff --git` line."""
        diff_text = (
            "diff --git itambox/assets/models.py itambox/assets/models.py\n"
            "--- itambox/assets/models.py\n"
            "+++ itambox/assets/models.py\n"
            "@@ -7,0 +8,1 @@\n"
            "+    added = 1\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/models.py": {8}})

    def test_git_quoted_non_ascii_paths_are_decoded(self):
        """`core.quotePath` is on by default: a non-ASCII path arrives octal-escaped."""
        diff_text = (
            'diff --git "a/itambox/assets/mod\\303\\250le.py" "b/itambox/assets/mod\\303\\250le.py"\n'
            '--- "a/itambox/assets/mod\\303\\250le.py"\n'
            '+++ "b/itambox/assets/mod\\303\\250le.py"\n'
            "@@ -1,0 +2,1 @@\n"
            "+    added = 1\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/modèle.py": {2}})

    def test_unreadable_quoted_path_escape_fails_closed(self):
        diff_text = (
            'diff --git "a/itambox/assets/bad\\q.py" "b/itambox/assets/bad\\q.py"\n'
            '--- "a/itambox/assets/bad\\q.py"\n'
            '+++ "b/itambox/assets/bad\\q.py"\n'
            "@@ -1,0 +2,1 @@\n"
            "+    added = 1\n"
        )

        with self.assertRaisesRegex(PolicyError, "unreadable escape sequence"):
            parse_diff(diff_text)

    def test_non_utf8_quoted_path_fails_closed(self):
        diff_text = (
            'diff --git "a/itambox/assets/bad\\377.py" "b/itambox/assets/bad\\377.py"\n'
            '--- "a/itambox/assets/bad\\377.py"\n'
            '+++ "b/itambox/assets/bad\\377.py"\n'
            "@@ -1,0 +2,1 @@\n"
            "+    added = 1\n"
        )

        with self.assertRaisesRegex(PolicyError, "does not decode as UTF-8"):
            parse_diff(diff_text)

    def test_an_unanchored_diff_is_refused_rather_than_read(self):
        diff_text = "--- itambox/assets/models.py\n+++ itambox/assets/models.py\n@@ -7,0 +8,1 @@\n+    added = 1\n"

        with self.assertRaises(PolicyError) as caught:
            parse_changed_lines(diff_text)
        self.assertIn("outside any `diff --git` section", str(caught.exception))

    def test_an_added_line_that_looks_like_a_header_does_not_reattribute_later_hunks(self):
        diff_text = (
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ -3,0 +4,1 @@\n"
            '+++ b/spoofed.py "an added line beginning with two plus signs"\n'
            "@@ -20,0 +21,2 @@\n"
            "+    first = 1\n"
            "+    second = 2\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/models.py": {4, 21, 22}})

    def test_a_removed_and_added_line_pair_that_looks_like_a_file_header_is_hunk_content(self):
        """The pair is what a diff of a diff fixture -- like this file -- produces."""
        diff_text = (
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ -3,1 +4,1 @@\n"
            "--- a/spoofed.py\n"
            "+++ b/spoofed.py\n"
            "@@ -20,0 +21,2 @@\n"
            "+    first = 1\n"
            "+    second = 2\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/models.py": {4, 21, 22}})

    def test_a_rename_attributes_changed_lines_to_the_destination_path(self):
        diff_text = (
            "diff --git a/itambox/assets/old_name.py b/itambox/assets/new_name.py\n"
            "similarity index 92%\n"
            "rename from itambox/assets/old_name.py\n"
            "rename to itambox/assets/new_name.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/itambox/assets/old_name.py\n"
            "+++ b/itambox/assets/new_name.py\n"
            "@@ -12,0 +13,2 @@\n"
            "+    first = 1\n"
            "+    second = 2\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/new_name.py": {13, 14}})

    def test_a_pure_rename_and_a_mode_only_change_contribute_no_lines(self):
        """Neither authors a post-change line, so neither is anyone's to cover."""
        diff_text = (
            "diff --git a/itambox/assets/old_name.py b/itambox/assets/renamed.py\n"
            "similarity index 100%\n"
            "rename from itambox/assets/old_name.py\n"
            "rename to itambox/assets/renamed.py\n"
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {})

    def test_a_binary_python_change_is_recorded_as_opaque_not_dropped(self):
        """A `.py` file marked `-diff` in .gitattributes diffs exactly like this."""
        diff_text = (
            "diff --git a/itambox/assets/opaque.py b/itambox/assets/opaque.py\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/itambox/assets/opaque.py and b/itambox/assets/opaque.py differ\n"
        )

        result = parse_diff(diff_text)

        self.assertEqual(result.changed, {})
        self.assertIn("itambox/assets/opaque.py", result.opaque)
        self.assertIn("without a line-level diff", result.opaque["itambox/assets/opaque.py"])

    def test_a_binary_quoted_python_path_with_spaces_is_still_opaque(self):
        diff_text = (
            'diff --git "a/itambox/assets/caf\\303\\251 module.py" '
            '"b/itambox/assets/caf\\303\\251 module.py"\n'
            "index 1111111..2222222 100644\n"
            'Binary files "a/itambox/assets/caf\\303\\251 module.py" and '
            '"b/itambox/assets/caf\\303\\251 module.py" differ\n'
        )

        result = parse_diff(diff_text)

        self.assertEqual(result.changed, {})
        self.assertIn("itambox/assets/café module.py", result.opaque)

    def test_an_unquoted_path_with_spaces_is_parsed(self):
        diff_text = (
            "diff --git a/itambox/assets/my module.py b/itambox/assets/my module.py\n"
            "--- a/itambox/assets/my module.py\t\n"
            "+++ b/itambox/assets/my module.py\t\n"
            "@@ -1,0 +2,1 @@\n"
            "+    added = 1\n"
        )

        self.assertEqual(parse_changed_lines(diff_text), {"itambox/assets/my module.py": {2}})

    def test_an_unquoted_binary_path_with_spaces_is_opaque(self):
        diff_text = (
            "diff --git a/itambox/assets/my module.py b/itambox/assets/my module.py\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/itambox/assets/my module.py and b/itambox/assets/my module.py differ\n"
        )

        self.assertIn("itambox/assets/my module.py", parse_diff(diff_text).opaque)

    def test_a_git_binary_patch_section_is_recorded_as_opaque(self):
        diff_text = (
            "diff --git a/itambox/assets/opaque.py b/itambox/assets/opaque.py\n"
            "new file mode 100644\n"
            "index 0000000..2222222\n"
            "GIT binary patch\n"
            "literal 12\n"
            "TcmZQzU|?Yo0Rn;40|Nsi0ssI2\n"
            "\n"
        )

        self.assertEqual(list(parse_diff(diff_text).opaque), ["itambox/assets/opaque.py"])

    def test_an_unreadable_header_line_is_refused_rather_than_skipped(self):
        """Silently skipping it would silently skip whatever it describes."""
        diff_text = (
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "invented header line 42\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ -7,0 +8,1 @@\n"
            "+    added = 1\n"
        )

        with self.assertRaises(PolicyError) as caught:
            parse_diff(diff_text)
        self.assertIn("unrecognised line", str(caught.exception))

    def test_binary_section_without_a_resolvable_path_fails_closed(self):
        diff_text = "diff --git a/old name.py b/new name.py\nBinary files a/old name.py and b/new name.py differ\n"

        with self.assertRaisesRegex(PolicyError, "names no post-change file"):
            parse_diff(diff_text)

    def test_an_unreadable_hunk_header_is_refused(self):
        diff_text = (
            "diff --git a/itambox/assets/models.py b/itambox/assets/models.py\n"
            "--- a/itambox/assets/models.py\n"
            "+++ b/itambox/assets/models.py\n"
            "@@ mangled @@\n"
        )

        with self.assertRaises(PolicyError) as caught:
            parse_diff(diff_text)
        self.assertIn("unparseable hunk header", str(caught.exception))


class LineRangeFormattingTests(unittest.TestCase):
    def test_consecutive_numbers_are_compressed_into_ranges(self):
        self.assertEqual(format_line_ranges([1, 2, 3, 7, 9, 10]), "1-3, 7, 9-10")

    def test_no_numbers_render_as_an_empty_string(self):
        self.assertEqual(format_line_ranges([]), "")


class FileClassificationTests(unittest.TestCase):
    """Every changed Python file lands in exactly one documented bucket."""

    def test_documented_exemption_is_reported_with_its_reason(self):
        verdict = evaluate_file("scripts/x.py", {1, 2}, load_report({"assets/models.py": coverage_entry([1])}))

        self.assertEqual(verdict.status, "exempt")
        self.assertIn("repository tooling", verdict.detail)
        self.assertEqual(verdict.executable, 0)

    def test_generated_migrations_are_not_production_code(self):
        verdict = evaluate_file(
            "itambox/assets/migrations/0001_x.py",
            {1, 2},
            load_report({"assets/models.py": coverage_entry([1])}),
        )

        self.assertEqual(verdict.status, "non-production")

    def test_test_code_is_not_production_code(self):
        verdict = evaluate_file(
            "itambox/assets/tests/test_x.py",
            {1, 2},
            load_report({"assets/models.py": coverage_entry([1])}),
        )

        self.assertEqual(verdict.status, "non-production")

    def test_unexempted_path_outside_the_measured_tree_is_unmeasured(self):
        verdict = evaluate_file("tools/release.py", {1, 2}, load_report({"assets/models.py": coverage_entry([1])}))

        self.assertEqual(verdict.status, "unmeasured")
        self.assertIn("outside the measured tree", verdict.detail)

    def test_production_file_absent_from_the_report_is_unmeasured(self):
        verdict = evaluate_file(
            "itambox/assets/brand_new.py",
            {1, 2},
            load_report({"assets/models.py": coverage_entry([1])}),
        )

        self.assertEqual(verdict.status, "unmeasured")
        self.assertIn("never measured it", verdict.detail)

    def test_measured_file_reports_covered_and_uncovered_changed_lines(self):
        report = load_report({"assets/models.py": coverage_entry(executed=[10, 11], missing=[12, 13])})

        verdict = evaluate_file("itambox/assets/models.py", {10, 11, 12, 13}, report)

        self.assertEqual(verdict.status, "measured")
        self.assertEqual((verdict.executable, verdict.covered), (4, 2))
        self.assertEqual(verdict.uncovered_lines, [12, 13])
        self.assertEqual(verdict.rate, 50.0)


class BranchAwarenessTests(unittest.TestCase):
    """The heart of the gate: an executed line with an untaken branch is uncovered."""

    def test_executed_line_that_is_the_source_of_a_missing_branch_is_uncovered(self):
        report = load_report({"assets/models.py": coverage_entry(executed=[10, 11], missing_branches=[(11, 13)])})

        verdict = evaluate_file("itambox/assets/models.py", {10, 11}, report)

        self.assertEqual((verdict.executable, verdict.covered), (2, 1))
        self.assertEqual(verdict.uncovered_lines, [11])
        self.assertEqual(verdict.rate, 50.0)

    def test_a_missing_branch_on_an_unchanged_line_does_not_penalise_the_change(self):
        report = load_report({"assets/models.py": coverage_entry(executed=[10, 11, 40], missing_branches=[(40, 42)])})

        verdict = evaluate_file("itambox/assets/models.py", {10, 11}, report)

        self.assertEqual((verdict.executable, verdict.covered), (2, 2))
        self.assertEqual(verdict.uncovered_lines, [])


class ExecutabilityTests(unittest.TestCase):
    """Changed lines that carry no statement are neither covered nor uncovered."""

    def test_lines_that_are_neither_executed_nor_missing_are_not_executable(self):
        report = load_report({"assets/models.py": coverage_entry(executed=[10], missing=[13])})

        verdict = evaluate_file("itambox/assets/models.py", {10, 11, 12, 13}, report)

        self.assertEqual((verdict.executable, verdict.covered), (2, 1))
        self.assertEqual(verdict.uncovered_lines, [13])

    def test_excluded_lines_are_not_counted_as_changed_executable_lines(self):
        report = load_report({"assets/models.py": coverage_entry(executed=[10], excluded=[11, 12])})

        verdict = evaluate_file("itambox/assets/models.py", {10, 11, 12}, report)

        self.assertEqual((verdict.executable, verdict.covered), (1, 1))
        self.assertEqual(verdict.rate, 100.0)


class CommandLineTests(unittest.TestCase):
    """End-to-end exit codes, driven from files so no git history is touched."""

    def run_gate(self, root, diff_text, files, extra_arguments=(), write_coverage=True, branch_coverage=True):
        root = Path(root)
        diff_path = root / "change.diff"
        diff_path.write_text(diff_text, encoding="utf-8")
        coverage_path = root / "coverage.json"
        if write_coverage:
            coverage_path.write_text(
                json.dumps(coverage_document(files, branch_coverage=branch_coverage)), encoding="utf-8"
            )
        arguments = [
            "--diff-file",
            str(diff_path),
            "--coverage-json",
            str(coverage_path),
            "--pyproject",
            str(write_pyproject(root)),
            *extra_arguments,
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(arguments)
        return status, stdout.getvalue() + stderr.getvalue()

    def test_fully_covered_change_passes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status, output = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {"assets/models.py": coverage_entry(executed=[10, 11, 12, 13])},
            )

            self.assertEqual(status, 0, output)
            self.assertIn("100.00%", output)

    def test_change_below_the_target_fails_and_names_the_uncovered_lines(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status, output = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {"assets/models.py": coverage_entry(executed=[10, 11], missing=[12, 13])},
            )

            self.assertEqual(status, 1)
            self.assertIn(f"below the {DIFF_COVERAGE_TARGET:.2f}% target", output)
            self.assertIn("itambox/assets/models.py: 12-13", output)

    def test_unmeasured_production_file_fails_even_when_every_other_line_is_covered(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            diff_text = diff_for("itambox/assets/models.py", [(10, 4)]) + diff_for(
                "itambox/assets/brand_new.py", [(1, 3)]
            )

            status, output = self.run_gate(
                temporary_directory,
                diff_text,
                {"assets/models.py": coverage_entry(executed=[10, 11, 12, 13])},
            )

            self.assertEqual(status, 1)
            self.assertIn("never measured", output)
            self.assertIn("itambox/assets/brand_new.py", output)

    def test_a_binary_python_change_fails_closed_instead_of_disappearing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            diff_text = diff_for("itambox/assets/models.py", [(10, 4)]) + (
                "diff --git a/itambox/assets/opaque.py b/itambox/assets/opaque.py\n"
                "index 1111111..2222222 100644\n"
                "Binary files a/itambox/assets/opaque.py and b/itambox/assets/opaque.py differ\n"
            )

            status, output = self.run_gate(
                temporary_directory,
                diff_text,
                {"assets/models.py": coverage_entry(executed=[10, 11, 12, 13])},
            )

            self.assertEqual(status, 1)
            self.assertIn("itambox/assets/opaque.py", output)
            self.assertIn("without a line-level diff", output)

    def test_a_binary_change_to_a_non_python_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            diff_text = diff_for("itambox/assets/models.py", [(10, 4)]) + (
                "diff --git a/itambox/static/img/logo.png b/itambox/static/img/logo.png\n"
                "index 1111111..2222222 100644\n"
                "Binary files a/itambox/static/img/logo.png and b/itambox/static/img/logo.png differ\n"
            )

            status, output = self.run_gate(
                temporary_directory,
                diff_text,
                {"assets/models.py": coverage_entry(executed=[10, 11, 12, 13])},
            )

            self.assertEqual(status, 0, output)

    def test_a_change_touching_no_python_files_passes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status, output = self.run_gate(
                temporary_directory,
                diff_for("docs/development/test-coverage-policy.md", [(5, 3)]),
                {"assets/models.py": coverage_entry(executed=[10])},
            )

            self.assertEqual(status, 0, output)
            self.assertIn("no changed executable production line(s)", output)

    def test_only_exempt_and_non_production_python_changes_pass(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            diff_text = (
                diff_for("scripts/check_diff_coverage.py", [(10, 5)])
                + diff_for("itambox/assets/migrations/0002_x.py", [(1, 4)])
                + diff_for("itambox/assets/tests/test_models.py", [(1, 9)])
            )

            status, output = self.run_gate(
                temporary_directory,
                diff_text,
                {"assets/models.py": coverage_entry(executed=[10])},
            )

            self.assertEqual(status, 0, output)
            self.assertIn("exempt: scripts/check_diff_coverage.py", output)

    def test_a_missing_coverage_report_is_an_untrustworthy_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status, output = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {},
                write_coverage=False,
            )

            self.assertEqual(status, 2)
            self.assertIn("no coverage report", output)

    def test_a_line_only_coverage_report_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            status, output = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {"assets/models.py": coverage_entry(executed=[10, 11, 12, 13])},
                branch_coverage=False,
            )

            self.assertEqual(status, 2)
            self.assertIn("without branch measurement", output)

    def test_a_coverage_entry_without_missing_branches_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            entry = coverage_entry(executed=[10, 11, 12, 13])
            del entry["missing_branches"]

            status, output = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {"assets/models.py": entry},
            )

            self.assertEqual(status, 2)
            self.assertIn("missing_branches", output)

    def test_summary_file_records_the_uncovered_lines_as_markdown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = Path(temporary_directory) / "summary.md"

            status, _ = self.run_gate(
                temporary_directory,
                diff_for("itambox/assets/models.py", [(10, 4)]),
                {"assets/models.py": coverage_entry(executed=[10, 11], missing=[12, 13])},
                extra_arguments=["--summary-file", str(summary)],
            )
            markdown = summary.read_text(encoding="utf-8")

            self.assertEqual(status, 1)
            self.assertIn("### Differential coverage (changed production code)", markdown)
            self.assertIn("| File | Changed lines | Covered | Rate | Uncovered |", markdown)
            self.assertIn("| `itambox/assets/models.py` | 4 | 2 | 50.0% | 12-13 |", markdown)
            self.assertIn("FAIL", markdown)

    def test_target_override_is_honoured(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            diff_text = diff_for("itambox/assets/models.py", [(10, 4)])
            files = {"assets/models.py": coverage_entry(executed=[10, 11], missing=[12, 13])}

            below, _ = self.run_gate(temporary_directory, diff_text, files, extra_arguments=["--target", "60"])
            at_target, output = self.run_gate(temporary_directory, diff_text, files, extra_arguments=["--target", "50"])

            self.assertEqual(below, 1)
            self.assertEqual(at_target, 0, output)
            self.assertIn("target 50.00%", output)


if __name__ == "__main__":
    unittest.main()
