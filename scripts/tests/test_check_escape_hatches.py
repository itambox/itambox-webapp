import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_escape_hatches import (
    CANONICAL_PYTHON,
    POLICY_CATEGORIES,
    RULE_PROVIDER_SLOT,
    RULE_SOURCE_TEXT,
    RULE_UNSCOPED,
    PolicyError,
    collect_escape_hatches,
    compare_baseline,
    compute_policy_fingerprint,
    load_baseline,
    main,
    write_baseline,
)
from scripts.exception_policy import BROAD_EXCEPTION_NAMES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class ScanCase(unittest.TestCase):
    def scan(self, relative, body):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, relative, body)
            return collect_escape_hatches(root, ["itambox"])

    def rules(self, result):
        return sorted(identity[0] for identity in result.findings.elements())


class UnscopedTests(ScanCase):
    def test_base_manager_and_all_objects_are_found_in_gated_layers(self):
        for relative in (
            "itambox/assets/views.py",
            "itambox/assets/views/asset_views.py",
            "itambox/assets/forms/asset_form.py",
            "itambox/assets/api/serializers.py",
            "itambox/assets/services/timeline.py",
            "itambox/assets/services.py",
        ):
            with self.subTest(relative=relative):
                result = self.scan(
                    relative,
                    """
                    def go(Asset):
                        return Asset._base_manager.all(), Asset.all_objects.all()
                    """,
                )
                self.assertEqual(self.rules(result), [RULE_UNSCOPED, RULE_UNSCOPED])

    def test_models_and_managers_are_out_of_scope(self):
        result = self.scan(
            "itambox/core/managers.py",
            """
            def go(Asset):
                return Asset._base_manager.all()
            """,
        )
        self.assertEqual(result.findings, {})

    def test_tests_and_migrations_are_out_of_scope(self):
        for relative in ("itambox/assets/tests/test_views.py", "itambox/assets/migrations/0002_views.py"):
            with self.subTest(relative=relative):
                result = self.scan(relative, "x = Asset.all_objects\n")
                self.assertEqual(result.findings, {})

    def test_identity_carries_scope_and_expression_not_line_numbers(self):
        first = self.scan("itambox/a/views.py", "class V:\n    def get(self):\n        return Asset._base_manager\n")
        second = self.scan(
            "itambox/a/views.py", "\n\n# unrelated\nclass V:\n    def get(self):\n        return Asset._base_manager\n"
        )
        self.assertEqual(first.findings, second.findings)
        ((rule, path, context, statement),) = first.findings
        self.assertEqual(
            (rule, path, context, statement),
            (RULE_UNSCOPED, "itambox/a/views.py", "ClassDef:V/FunctionDef:get", "Asset._base_manager"),
        )

    def test_trailing_comment_justifies(self):
        result = self.scan(
            "itambox/a/views.py",
            "def go(A):\n    return A._base_manager  # unscoped: row-lock: re-read under lock\n",
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(dict(result.annotated), {(RULE_UNSCOPED, "row-lock"): 1})

    def test_comment_block_above_the_statement_justifies_a_multiline_query(self):
        result = self.scan(
            "itambox/a/views.py",
            """
            def go(A):
                # unscoped: recycle-bin: restore lists deleted rows
                return (
                    A.all_objects
                    .filter(deleted_at__isnull=False)
                )
            """,
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated[(RULE_UNSCOPED, "recycle-bin")], 1)

    def test_a_comment_does_not_leak_to_the_next_statement(self):
        result = self.scan(
            "itambox/a/views.py",
            """
            def go(A):
                a = A._base_manager  # unscoped: row-lock: reason
                b = A._base_manager
                return a, b
            """,
        )
        self.assertEqual(self.rules(result), [RULE_UNSCOPED])

    def test_unknown_category_and_missing_reason_are_malformed(self):
        for comment in ("# unscoped: because: reason", "# unscoped: row-lock", "# unscoped: just because"):
            with self.subTest(comment=comment):
                result = self.scan("itambox/a/views.py", f"def go(A):\n    return A._base_manager  {comment}\n")
                self.assertEqual(len(result.malformed), 1)
                self.assertEqual(result.findings, {})

    def test_another_rules_marker_does_not_justify(self):
        result = self.scan(
            "itambox/a/views.py",
            "def go(A):\n    return A._base_manager  # source-text: tooling: nope\n",
        )
        self.assertEqual(self.rules(result), [RULE_UNSCOPED])
        self.assertEqual(result.malformed, [])


class ProviderSlotTests(ScanCase):
    def test_plain_subscripted_aliased_and_attribute_instantiations(self):
        result = self.scan(
            "itambox/core/seam.py",
            """
            from core.provider_slot import SingleProviderSlot, SingleProviderSlot as _Slot
            import core.provider_slot as slots

            a = SingleProviderSlot[int]("a")
            b = _Slot[int]("b")
            c = slots.SingleProviderSlot("c")
            """,
        )
        self.assertEqual(self.rules(result), [RULE_PROVIDER_SLOT] * 3)

    def test_annotated_slot_is_justified_and_unrelated_calls_ignored(self):
        result = self.scan(
            "itambox/core/seam.py",
            """
            from core.provider_slot import SingleProviderSlot

            # provider-slot: plugin-seam: plugins register their own provider
            slot = SingleProviderSlot[int]("slot")
            other = dict()
            """,
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated[(RULE_PROVIDER_SLOT, "plugin-seam")], 1)

    def test_type_annotations_and_imports_alone_are_not_instances(self):
        result = self.scan(
            "itambox/core/seam.py",
            "from core.provider_slot import SingleProviderSlot\n\nslot: SingleProviderSlot\n",
        )
        self.assertEqual(result.findings, {})


class SourceTextTests(ScanCase):
    def test_getsource_and_ast_parse_in_test_modules(self):
        result = self.scan(
            "itambox/core/tests/test_shape.py",
            """
            import ast
            import inspect
            from inspect import getsource as gs
            from ast import parse

            def test_a(fn):
                inspect.getsource(fn)
                inspect.getsourcelines(fn)
                ast.parse("x")
                gs(fn)
                parse("y")
            """,
        )
        self.assertEqual(self.rules(result), [RULE_SOURCE_TEXT] * 5)

    def test_conftest_and_tests_py_count_as_test_modules(self):
        for relative in ("itambox/conftest.py", "itambox/app/tests.py", "itambox/app/test_x.py"):
            with self.subTest(relative=relative):
                result = self.scan(relative, "import ast\nast.parse('x')\n")
                self.assertEqual(self.rules(result), [RULE_SOURCE_TEXT])

    def test_production_modules_may_parse_source(self):
        result = self.scan("itambox/core/tool.py", "import ast\nast.parse('x')\n")
        self.assertEqual(result.findings, {})

    def test_unrelated_parse_and_unimported_names_are_ignored(self):
        result = self.scan(
            "itambox/core/tests/test_ok.py",
            """
            import json
            from urllib.parse import parse_qs

            def test_a(parser):
                parser.parse("x")
                json.loads("{}")
                parse("z")
            """,
        )
        self.assertEqual(result.findings, {})

    def test_scripts_tests_are_exempt_when_scripts_is_targeted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, "scripts/tests/test_gate.py", "import ast\nast.parse('x')\n")
            write(root, "scripts/tests/__init__.py", "")
            self.assertEqual(collect_escape_hatches(root, ["scripts"]).findings, {})

    def test_justified_source_text_test(self):
        result = self.scan(
            "itambox/core/tests/test_shape.py",
            "import ast\n\n\ndef test_a():\n    # source-text: static-contract: no runtime form\n    ast.parse('x')\n",
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(result.annotated[(RULE_SOURCE_TEXT, "static-contract")], 1)


class BaselineTests(ScanCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        write(self.root, "itambox/a/views.py", "def go(A):\n    return A._base_manager\n")
        self.baseline_path = self.root / "baseline.json"

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--cwd", str(self.root), "--baseline", str(self.baseline_path), *extra, "itambox"])
        return code, out.getvalue(), err.getvalue()

    @unittest.skipUnless(sys.version_info[:2] == CANONICAL_PYTHON, "canonical interpreter only")
    def test_bootstrap_then_clean_then_regression_then_stale(self):
        self.assertEqual(self.run_main("--write-baseline")[0], 0)
        self.assertEqual(self.run_main()[0], 0)

        write(self.root, "itambox/b/forms.py", "def go(B):\n    return B.all_objects\n")
        code, out, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("new occurrence(s) introduced", out)
        self.assertIn("# unscoped: <category>: <reason>", out)
        self.assertEqual(self.run_main("--write-baseline")[0], 1)

        (self.root / "itambox/b/forms.py").unlink()
        write(self.root, "itambox/a/views.py", "def go(A):\n    return 1\n")
        code, out, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("baseline is stale", out)
        self.assertEqual(self.run_main("--write-baseline")[0], 0)
        self.assertEqual(self.run_main()[0], 0)

    @unittest.skipUnless(sys.version_info[:2] == CANONICAL_PYTHON, "canonical interpreter only")
    def test_malformed_annotation_fails_the_gate(self):
        self.run_main("--write-baseline")
        write(self.root, "itambox/a/views.py", "def go(A):\n    return A._base_manager  # unscoped: nope: x\n")
        code, out, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("unusable justification", out)

    def test_baseline_must_match_the_policy_fingerprint_and_be_well_formed(self):
        findings = collect_escape_hatches(self.root, ["itambox"]).findings
        fingerprint = compute_policy_fingerprint(["itambox"])
        write_baseline(findings, self.baseline_path, fingerprint)
        self.assertEqual(load_baseline(self.baseline_path, fingerprint), findings)
        with self.assertRaises(PolicyError):
            load_baseline(self.baseline_path, "0" * 64)

        data = json.loads(self.baseline_path.read_text())
        data["findings"][0]["rule"] = "made-up"
        self.baseline_path.write_text(json.dumps(data))
        with self.assertRaises(PolicyError):
            load_baseline(self.baseline_path, fingerprint)

    def test_fingerprint_binds_targets(self):
        self.assertNotEqual(compute_policy_fingerprint(["itambox"]), compute_policy_fingerprint(["itambox", "x"]))

    def test_compare_baseline_is_a_multiset_diff(self):
        key = (RULE_UNSCOPED, "p", "c", "s")
        regressions, stale = compare_baseline({key: 2}, {key: 1})
        self.assertEqual((dict(regressions), dict(stale)), ({key: 1}, {}))

    def test_non_canonical_interpreter_is_refused(self):
        original = sys.version_info
        try:
            sys.version_info = (3, 11, 0, "final", 0)
            code, _, err = self.run_main()
        finally:
            sys.version_info = original
        self.assertEqual(code, 2)
        self.assertIn("Refusing to run", err)


class RepositoryContractTests(unittest.TestCase):
    def test_every_rule_has_categories(self):
        for rule in (RULE_UNSCOPED, RULE_PROVIDER_SLOT, RULE_SOURCE_TEXT):
            self.assertTrue(POLICY_CATEGORIES[rule])

    def test_broad_except_stays_with_the_exception_gate(self):
        self.assertEqual({"Exception", "BaseException"}, set(BROAD_EXCEPTION_NAMES))

    @unittest.skipUnless(sys.version_info[:2] == CANONICAL_PYTHON, "canonical interpreter only")
    def test_committed_baseline_matches_the_repository(self):
        fingerprint = compute_policy_fingerprint(["itambox"])
        baseline = load_baseline(REPOSITORY_ROOT / "scripts" / "escape_hatch_baseline.json", fingerprint)
        result = collect_escape_hatches(REPOSITORY_ROOT, ["itambox"])
        self.assertEqual(result.malformed, [])
        regressions, stale = compare_baseline(result.findings, baseline)
        self.assertEqual((dict(regressions), dict(stale)), ({}, {}))

    def test_agents_md_documents_the_policy(self):
        text = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for needle in ("check_escape_hatches.py", "# unscoped:", "# provider-slot:", "# source-text:"):
            self.assertIn(needle, text)
