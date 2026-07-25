import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_local_imports import (
    CANONICAL_PYTHON,
    POLICY_CATEGORIES,
    SCHEMA_VERSION,
    PolicyError,
    collect_local_imports,
    compare_baseline,
    compute_policy_fingerprint,
    load_baseline,
    main,
    write_baseline,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class ScanTests(unittest.TestCase):
    """The scanner sees function-body imports and nothing else."""

    def scan(self, body, relative="itambox/core/sample.py"):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, relative, body)
            return collect_local_imports(root, ["itambox"])

    def test_module_level_imports_are_out_of_scope(self):
        result = self.scan(
            """
            import os
            from django.db import models

            if True:
                import json


            class Thing:
                import csv

                def method(self):
                    return os, models, json, csv
            """
        )

        self.assertEqual(result.findings, {})
        self.assertEqual(result.malformed, [])

    def test_function_body_imports_are_collected_with_scope_context(self):
        result = self.scan(
            """
            def outer():
                import os

                class Inner:
                    def method(self):
                        from django.db import models

                        return models

                return os, Inner
            """
        )

        self.assertEqual(
            sorted(key[1:3] for key in result.findings),
            [
                ("FunctionDef:outer", "import os"),
                (
                    "FunctionDef:outer/ClassDef:Inner/FunctionDef:method",
                    "from django.db import models",
                ),
            ],
        )

    def test_async_functions_and_repeated_statements_are_counted(self):
        result = self.scan(
            """
            async def handler(flag):
                if flag:
                    from core.models import Thing
                else:
                    from core.models import Thing

                return Thing
            """
        )

        self.assertEqual(list(result.findings.values()), [2])
        self.assertEqual(
            list(result.findings)[0][1:3],
            ("AsyncFunctionDef:handler", "from core.models import Thing"),
        )

    def test_identity_ignores_line_numbers_and_formatting(self):
        first = self.scan(
            """
            def build():
                from core.models import (
                    Thing,
                )

                return Thing
            """
        )
        second = self.scan(
            """
            # an unrelated comment added above the function


            def build():
                from core.models import Thing

                return Thing
            """
        )

        self.assertEqual(first.findings, second.findings)

    def test_relative_imports_are_recorded_verbatim(self):
        result = self.scan(
            """
            def build():
                from .services import adjust

                return adjust
            """
        )

        self.assertEqual(list(result.findings)[0][2], "from .services import adjust")


class ExclusionTests(unittest.TestCase):
    """Scope boundaries: production code only."""

    def collect(self, files, targets=("itambox", "scripts")):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for relative, body in files.items():
                write(root, relative, body)
            return collect_local_imports(root, list(targets))

    def test_tests_migrations_and_vendor_paths_are_excluded(self):
        body = """
            def build():
                import os

                return os
            """
        result = self.collect(
            {
                "itambox/assets/tests/test_thing.py": body,
                "itambox/assets/tests.py": body,
                "itambox/assets/test_other.py": body,
                "itambox/conftest.py": body,
                "itambox/assets/migrations/0001_initial.py": body,
                "itambox/static/dist/generated.py": body,
                "itambox/node_modules/package/thing.py": body,
                "itambox/assets/models.py": body,
            }
        )

        self.assertEqual([key[0] for key in result.findings], ["itambox/assets/models.py"])

    def test_files_outside_the_configured_targets_are_ignored(self):
        result = self.collect(
            {
                "itambox/assets/models.py": "def build():\n    import os\n\n    return os\n",
                "other/thing.py": "def build():\n    import os\n\n    return os\n",
            },
            targets=("itambox",),
        )

        self.assertEqual([key[0] for key in result.findings], ["itambox/assets/models.py"])

    def test_unparsable_source_fails_closed(self):
        with self.assertRaises(PolicyError):
            self.collect({"itambox/assets/broken.py": "def build(:\n"})


class AnnotationTests(unittest.TestCase):
    """Justified imports carry a machine-checkable reason."""

    def scan(self, body):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, "itambox/core/sample.py", body)
            return collect_local_imports(root, ["itambox"])

    def test_trailing_annotation_marks_an_import_as_justified(self):
        result = self.scan(
            """
            def build():
                from core.auth import cache  # inline import: cycle: core.managers <-> core.auth

                return cache
            """
        )

        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated["cycle"], 1)

    def test_preceding_comment_annotation_is_accepted(self):
        result = self.scan(
            """
            def build():
                # inline import: app-registry: models are not loadable at import time
                from organization.models import Tenant

                return Tenant
            """
        )

        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated["app-registry"], 1)

    def test_one_annotation_covers_a_contiguous_import_group(self):
        result = self.scan(
            """
            def build():
                # inline imports: cycle: core.auth <-> organization at load time
                from organization.models import Tenant
                from organization.rbac import applicable_grants

                return Tenant, applicable_grants
            """
        )

        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated["cycle"], 2)

    def test_a_blank_line_ends_the_annotated_group(self):
        result = self.scan(
            """
            def build():
                # inline import: cycle: core.auth <-> organization at load time
                from organization.models import Tenant

                from organization.rbac import applicable_grants

                return Tenant, applicable_grants
            """
        )

        self.assertEqual([key[2] for key in result.findings], ["from organization.rbac import applicable_grants"])
        self.assertEqual(result.annotated["cycle"], 1)

    def test_group_inheritance_never_crosses_a_scope_boundary(self):
        result = self.scan(
            """
            def build(): import os  # inline import: cycle: core.a <-> core.b
            def other(): import sys
            """
        )

        self.assertEqual([key[1:3] for key in result.findings], [("FunctionDef:other", "import sys")])
        self.assertEqual(result.annotated["cycle"], 1)

    def test_every_documented_category_is_accepted(self):
        for category in sorted(POLICY_CATEGORIES):
            with self.subTest(category=category):
                result = self.scan(
                    f"""
                    def build():
                        import os  # inline import: {category}: documented reason

                        return os
                    """
                )

                self.assertEqual(result.findings, {})
                self.assertEqual(result.annotated[category], 1)

    def test_unknown_category_is_a_policy_error(self):
        result = self.scan(
            """
            def build():
                import os  # inline import: convenience: it reads nicer here

                return os
            """
        )

        self.assertEqual(len(result.malformed), 1)
        self.assertIn("convenience", result.malformed[0].problem)

    def test_marker_without_category_or_reason_is_a_policy_error(self):
        for comment in (
            "# inline import: avoids a circular import",
            "# inline import: cycle:",
            "# inline import:",
        ):
            with self.subTest(comment=comment):
                result = self.scan(
                    f"""
                    def build():
                        import os  {comment}

                        return os
                    """
                )

                self.assertEqual(len(result.malformed), 1)

    def test_unrelated_comments_do_not_annotate(self):
        result = self.scan(
            """
            def build():
                # Resolve the tenant before scoping the queryset.
                import os

                return os
            """
        )

        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.malformed, [])

    def test_module_level_annotation_marker_is_not_a_policy_error(self):
        result = self.scan(
            """
            import os  # inline import: nonsense: module level is out of scope


            def build():
                return os
            """
        )

        self.assertEqual(result.malformed, [])


class BaselineTests(unittest.TestCase):
    """Identity ratchet semantics: regressions and stale entries both fail."""

    def make_baseline(self, findings, path, fingerprint):
        write_baseline(findings, path, fingerprint)
        return load_baseline(path, fingerprint)

    def test_round_trip_is_stable_and_sorted(self):
        findings = {
            ("itambox/b.py", "FunctionDef:b", "import os"): 1,
            ("itambox/a.py", "FunctionDef:a", "import sys"): 2,
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            with redirect_stdout(io.StringIO()):
                loaded = self.make_baseline(findings, path, "fingerprint")

            self.assertEqual(loaded, findings)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)
            self.assertEqual(raw["policy_sha256"], "fingerprint")
            self.assertEqual([row["path"] for row in raw["findings"]], ["itambox/a.py", "itambox/b.py"])
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_new_identity_is_a_regression_and_removed_identity_is_stale(self):
        baseline = {("itambox/a.py", "FunctionDef:a", "import os"): 2}
        findings = {
            ("itambox/a.py", "FunctionDef:a", "import os"): 1,
            ("itambox/a.py", "FunctionDef:a", "import sys"): 1,
        }

        regressions, stale = compare_baseline(findings, baseline)

        self.assertEqual(regressions, {("itambox/a.py", "FunctionDef:a", "import sys"): 1})
        self.assertEqual(stale, {("itambox/a.py", "FunctionDef:a", "import os"): 1})

    def test_baseline_bound_to_the_policy_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            with redirect_stdout(io.StringIO()):
                write_baseline({}, path, "fingerprint")

            with self.assertRaises(PolicyError):
                load_baseline(path, "a-different-fingerprint")

    def test_policy_fingerprint_tracks_the_effective_policy(self):
        self.assertNotEqual(
            compute_policy_fingerprint(["itambox", "scripts"]),
            compute_policy_fingerprint(["itambox"]),
        )
        self.assertEqual(
            compute_policy_fingerprint(["itambox"]),
            compute_policy_fingerprint(["itambox"]),
        )

    def test_malformed_baselines_are_rejected(self):
        fingerprint = compute_policy_fingerprint(["itambox"])
        valid_row = {
            "path": "itambox/a.py",
            "context": "FunctionDef:a",
            "statement": "import os",
            "count": 1,
        }
        cases = {
            "unknown schema": {"schema_version": SCHEMA_VERSION + 1},
            "wrong python": {"canonical_python": "3.11"},
            "findings not a list": {"findings": {}},
            "row missing field": {"findings": [{k: v for k, v in valid_row.items() if k != "count"}]},
            "row extra field": {"findings": [{**valid_row, "extra": 1}]},
            "zero count": {"findings": [{**valid_row, "count": 0}]},
            "boolean count": {"findings": [{**valid_row, "count": True}]},
            "non-string identity": {"findings": [{**valid_row, "path": 1}]},
            "duplicate identity": {"findings": [valid_row, valid_row]},
            "unsorted": {
                "findings": [
                    {**valid_row, "path": "itambox/b.py"},
                    valid_row,
                ]
            },
        }
        for label, override in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "baseline.json"
                document = {
                    "schema_version": SCHEMA_VERSION,
                    "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
                    "policy_sha256": fingerprint,
                    "findings": [valid_row],
                }
                document.update(override)
                path.write_text(json.dumps(document), encoding="utf-8")

                with self.assertRaises(PolicyError):
                    load_baseline(path, fingerprint)


class CommandLineTests(unittest.TestCase):
    """End-to-end exit codes on a synthetic checkout."""

    def build_checkout(self, root, body):
        write(root, "itambox/core/sample.py", body)

    def run_main(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(arguments)
        return status, stdout.getvalue() + stderr.getvalue()

    def prepare(self, root, body):
        self.build_checkout(root, body)
        baseline = root / "baseline.json"
        status, _ = self.run_main(
            [
                "itambox",
                "--write-baseline",
                "--baseline",
                str(baseline),
                "--cwd",
                str(root),
            ]
        )
        self.assertEqual(status, 0)
        return baseline

    def check(self, root, baseline):
        return self.run_main(["itambox", "--baseline", str(baseline), "--cwd", str(root)])

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_clean_tree_passes_and_new_import_regresses(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = self.prepare(
                root,
                """
                def build():
                    import os

                    return os
                """,
            )

            status, _ = self.check(root, baseline)
            self.assertEqual(status, 0)

            self.build_checkout(
                root,
                """
                def build():
                    import os
                    import sys

                    return os, sys
                """,
            )
            status, output = self.check(root, baseline)

            self.assertEqual(status, 1)
            self.assertIn("import sys", output)
            self.assertIn("new function-body import", output)

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_removed_debt_makes_the_baseline_stale(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = self.prepare(
                root,
                """
                def build():
                    import os

                    return os
                """,
            )
            self.build_checkout(root, "import os\n\n\ndef build():\n    return os\n")

            status, output = self.check(root, baseline)

            self.assertEqual(status, 1)
            self.assertIn("stale", output)

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_annotating_an_import_removes_it_from_the_debt_ratchet(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = self.prepare(
                root,
                """
                def build():
                    import os

                    return os
                """,
            )
            self.build_checkout(
                root,
                """
                def build():
                    import os  # inline import: heavy-import: keeps module load cheap

                    return os
                """,
            )

            status, output = self.check(root, baseline)

            self.assertEqual(status, 1)
            self.assertIn("stale", output)

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_malformed_annotation_fails_and_blocks_baseline_writes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = self.prepare(root, "def build():\n    return None\n")
            self.build_checkout(
                root,
                """
                def build():
                    import os  # inline import: whatever: not a documented category

                    return os
                """,
            )

            status, output = self.check(root, baseline)
            self.assertEqual(status, 1)
            self.assertIn("unrecognised justification", output)

            status, _ = self.run_main(
                [
                    "itambox",
                    "--write-baseline",
                    "--baseline",
                    str(baseline),
                    "--cwd",
                    str(root),
                ]
            )
            self.assertEqual(status, 1)

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_write_baseline_refuses_to_grandfather_new_debt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = self.prepare(root, "def build():\n    return None\n")
            self.build_checkout(root, "def build():\n    import os\n\n    return os\n")

            status, output = self.run_main(
                [
                    "itambox",
                    "--write-baseline",
                    "--baseline",
                    str(baseline),
                    "--cwd",
                    str(root),
                ]
            )

            self.assertEqual(status, 1)
            self.assertIn("import os", output)


class RepositoryPolicyTests(unittest.TestCase):
    """The checked-in baseline describes this working tree."""

    @unittest.skipUnless(
        sys.version_info[:2] == CANONICAL_PYTHON,
        "the gate refuses to run outside canonical Python",
    )
    def test_checked_in_baseline_matches_the_repository(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stdout):
            status = main([])

        self.assertEqual(status, 0, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
