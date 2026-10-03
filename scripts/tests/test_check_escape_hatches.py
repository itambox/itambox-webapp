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
    RULES,
    SCHEMA_VERSION,
    PolicyError,
    collect_findings,
    compare_baseline,
    compute_policy_fingerprint,
    load_baseline,
    main,
    write_baseline,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
canonical_only = unittest.skipUnless(
    sys.version_info[:2] == CANONICAL_PYTHON, "the gate refuses to run outside canonical Python"
)


def write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class ScanCase(unittest.TestCase):
    def scan(self, body, relative):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, relative, body)
            return collect_findings(root, ["itambox", "scripts"])

    def rules(self, result):
        return sorted(key[0] for key in result.findings.elements())


class UnscopedRuleTests(ScanCase):
    BODY = """
        def get(pk):
            return Thing._base_manager.get(pk=pk)

        def listing():
            return Thing.all_objects.all()
        """

    def test_views_forms_serializers_and_services_are_governed(self):
        for relative in (
            "itambox/assets/views.py",
            "itambox/assets/views/detail.py",
            "itambox/assets/forms/asset_form.py",
            "itambox/assets/membership_form.py",
            "itambox/assets/api/serializers.py",
            "itambox/assets/services.py",
            "itambox/extras/services/events.py",
            "itambox/extras/provider_views.py",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(self.rules(self.scan(self.BODY, relative)), ["unscoped", "unscoped"])

    def test_other_layers_and_tests_are_not_governed(self):
        for relative in (
            "itambox/assets/models.py",
            "itambox/core/managers.py",
            "itambox/assets/signals.py",
            "itambox/assets/tests/test_views.py",
            "itambox/assets/test_services.py",
            "itambox/assets/migrations/0001_services.py",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(self.scan(self.BODY, relative).findings, {})

    def test_identity_is_scope_and_expression_not_line_number(self):
        first = self.scan(self.BODY, "itambox/assets/views.py")
        second = self.scan("\n\n# moved\n" + textwrap.dedent(self.BODY), "itambox/assets/views.py")

        self.assertEqual(first.findings, second.findings)
        self.assertIn(
            ("unscoped", "itambox/assets/views.py", "FunctionDef:get", "Thing._base_manager"),
            first.findings,
        )

    def test_repeated_use_in_one_scope_is_counted(self):
        result = self.scan(
            """
            def both():
                Thing._base_manager.all()
                Thing._base_manager.all()
            """,
            "itambox/assets/views.py",
        )

        self.assertEqual(list(result.findings.values()), [2])

    def test_text_mentions_are_not_attribute_uses(self):
        result = self.scan(
            '''
            def doc():
                """Never use Thing._base_manager here."""
                return "all_objects"  # all_objects
            ''',
            "itambox/assets/views.py",
        )

        self.assertEqual(result.findings, {})


class ProviderSlotRuleTests(ScanCase):
    def test_instances_are_found_however_the_name_is_bound(self):
        result = self.scan(
            """
            from core.provider_slot import SingleProviderSlot, SingleProviderSlot as _Slot
            import core.provider_slot as slots

            a = SingleProviderSlot[int]("a")
            b = _SingleProviderSlot[int]("b")
            c = slots.SingleProviderSlot("c")
            """,
            "itambox/core/example.py",
        )

        self.assertEqual(self.rules(result), ["provider-slot"] * 3)

    def test_importing_or_annotating_the_type_is_not_an_instance(self):
        result = self.scan(
            """
            from core.provider_slot import SingleProviderSlot

            def accepts(slot: SingleProviderSlot) -> None:
                return None
            """,
            "itambox/core/example.py",
        )

        self.assertEqual(result.findings, {})

    def test_test_modules_may_build_slots(self):
        result = self.scan(
            'slot = SingleProviderSlot[int]("fixture")\n',
            "itambox/core/tests/test_slot.py",
        )

        self.assertEqual(result.findings, {})


class SourceTextRuleTests(ScanCase):
    BODY = """
        import ast
        import inspect
        from inspect import getsource

        def test_shape():
            inspect.getsource(thing)
            inspect.getsourcelines(thing)
            getsource(thing)
            ast.parse("x = 1")
        """

    def test_test_modules_are_governed(self):
        for relative in (
            "itambox/core/tests/test_a.py",
            "itambox/core/tests.py",
            "itambox/core/test_b.py",
            "itambox/conftest.py",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(self.rules(self.scan(self.BODY, relative)), ["source-text"] * 4)

    def test_gate_suites_and_production_code_are_not_governed(self):
        for relative in ("scripts/tests/test_gate.py", "itambox/core/parsing.py"):
            with self.subTest(relative=relative):
                self.assertEqual(self.scan(self.BODY, relative).findings, {})

    def test_other_modules_with_a_parse_method_do_not_match(self):
        result = self.scan("def test_x():\n    json.parse(x)\n    self.parse(y)\n", "itambox/core/tests/test_a.py")

        self.assertEqual(result.findings, {})


class AnnotationTests(ScanCase):
    RELATIVE = "itambox/assets/views.py"

    def test_trailing_comment_justifies(self):
        result = self.scan(
            "def f():\n    return Thing._base_manager.all()  # unscoped: restore flow\n",
            self.RELATIVE,
        )

        self.assertEqual(result.findings, {})
        self.assertEqual(dict(result.annotated), {"unscoped": 1})

    def test_preceding_comment_block_justifies(self):
        result = self.scan(
            """
            def f():
                # unscoped: the recycle bin lists every tenant's
                # soft-deleted rows for operators
                return Thing.all_objects.all()
            """,
            self.RELATIVE,
        )

        self.assertEqual(result.findings, {})

    def test_comment_on_any_line_of_a_multiline_statement_justifies(self):
        result = self.scan(
            """
            def f():
                return (
                    Thing._base_manager  # unscoped: lock the row across tenants
                    .select_for_update()
                    .get(pk=1)
                )
            """,
            self.RELATIVE,
        )

        self.assertEqual(result.findings, {})

    def test_a_block_comment_does_not_leak_into_a_compound_statements_body(self):
        result = self.scan(
            """
            def f(items):
                for item in items:  # unrelated
                    # unscoped: only this line
                    Thing._base_manager.all()
                    Thing._base_manager.count()
            """,
            self.RELATIVE,
        )

        self.assertEqual(sum(result.findings.values()), 1)

    def test_a_comment_after_a_blank_line_does_not_justify(self):
        result = self.scan(
            "def f():\n    # unscoped: stray\n\n    return Thing._base_manager.all()\n",
            self.RELATIVE,
        )

        self.assertEqual(sum(result.findings.values()), 1)

    def test_a_marker_for_another_rule_does_not_justify(self):
        result = self.scan(
            "def f():\n    return Thing._base_manager.all()  # source-text: wrong rule\n",
            self.RELATIVE,
        )

        self.assertEqual(sum(result.findings.values()), 1)

    def test_each_rule_accepts_its_own_marker(self):
        cases = {
            "provider-slot": ("itambox/core/example.py", 'slot = SingleProviderSlot[int]("a")  # provider-slot: x\n'),
            "source-text": ("itambox/core/tests/test_a.py", "import ast\nast.parse('')  # source-text: x\n"),
        }
        for rule, (relative, body) in cases.items():
            with self.subTest(rule=rule):
                result = self.scan(body, relative)
                self.assertEqual(result.findings, {})
                self.assertEqual(dict(result.annotated), {rule: 1})

    def test_marker_without_a_reason_is_malformed(self):
        result = self.scan(
            "def f():\n    return Thing._base_manager.all()  # unscoped:\n",
            self.RELATIVE,
        )

        self.assertEqual(len(result.malformed), 1)
        self.assertEqual(result.findings, {})

    def test_unparsable_source_fails_closed(self):
        with self.assertRaises(PolicyError):
            self.scan("def broken(:\n", self.RELATIVE)


class BaselineTests(unittest.TestCase):
    def fingerprint(self):
        return compute_policy_fingerprint(["itambox", "scripts"])

    def test_round_trip_is_sorted_and_stable(self):
        findings = {
            ("unscoped", "itambox/b/views.py", "FunctionDef:f", "T._base_manager"): 2,
            ("provider-slot", "itambox/a.py", "", 'SingleProviderSlot[int]("a")'): 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            with redirect_stdout(io.StringIO()):
                write_baseline(findings, path, self.fingerprint())
            loaded = load_baseline(path, self.fingerprint())
            rows = json.loads(path.read_text(encoding="utf-8"))["findings"]

        self.assertEqual(dict(loaded), findings)
        self.assertEqual([row["rule"] for row in rows], ["provider-slot", "unscoped"])

    def test_new_identity_regresses_and_removed_identity_is_stale(self):
        regressions, stale = compare_baseline({"new": 1, "kept": 1}, {"kept": 1, "gone": 1})

        self.assertEqual(dict(regressions), {"new": 1})
        self.assertEqual(dict(stale), {"gone": 1})

    def test_baseline_is_bound_to_the_policy_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            with redirect_stdout(io.StringIO()):
                write_baseline({}, path, self.fingerprint())
            with self.assertRaises(PolicyError):
                load_baseline(path, "0" * 64)

    def test_fingerprint_tracks_targets(self):
        self.assertNotEqual(compute_policy_fingerprint(["itambox"]), compute_policy_fingerprint(["scripts"]))

    def test_malformed_baselines_are_rejected(self):
        header = {
            "schema_version": SCHEMA_VERSION,
            "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
            "policy_sha256": self.fingerprint(),
        }
        row = {"rule": "unscoped", "path": "a/views.py", "context": "", "statement": "T._base_manager", "count": 1}
        cases = {
            "not json": "{",
            "extra field": {**header, "findings": [], "extra": 1},
            "wrong schema": {**header, "schema_version": 99, "findings": []},
            "wrong python": {**header, "canonical_python": "3.11", "findings": []},
            "findings not a list": {**header, "findings": {}},
            "unknown rule": {**header, "findings": [{**row, "rule": "other"}]},
            "bad count": {**header, "findings": [{**row, "count": 0}]},
            "bool count": {**header, "findings": [{**row, "count": True}]},
            "bad fields": {**header, "findings": [{"rule": "unscoped"}]},
            "non-string identity": {**header, "findings": [{**row, "path": 1}]},
            "duplicate": {**header, "findings": [row, row]},
            "unsorted": {
                **header,
                "findings": [row, {**row, "rule": "provider-slot"}],
            },
        }
        for name, payload in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "baseline.json"
                path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    load_baseline(path, self.fingerprint())

    def test_missing_baseline_is_an_error(self):
        with self.assertRaises(PolicyError):
            load_baseline(Path("/nonexistent/baseline.json"), self.fingerprint())


class CommandLineTests(unittest.TestCase):
    VIEW = "itambox/assets/views.py"

    def run_main(self, root, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["itambox", "--baseline", str(root / "baseline.json"), "--cwd", str(root), *extra])
        return status, stdout.getvalue() + stderr.getvalue()

    def prepare(self, root, body="def f():\n    return Thing._base_manager.all()\n"):
        write(root, self.VIEW, body)
        status, _ = self.run_main(root, "--write-baseline")
        self.assertEqual(status, 0)

    @canonical_only
    def test_clean_tree_passes_and_a_new_occurrence_regresses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            self.assertEqual(self.run_main(root)[0], 0)

            write(
                root,
                self.VIEW,
                "def f():\n    return Thing._base_manager.all()\n\ndef g():\n    return X.all_objects\n",
            )
            status, output = self.run_main(root)

        self.assertEqual(status, 1)
        self.assertIn("[unscoped]", output)
        self.assertIn("FunctionDef:g", output)

    @canonical_only
    def test_paid_down_debt_makes_the_baseline_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            write(root, self.VIEW, "def f():\n    return Thing.objects.all()\n")
            status, output = self.run_main(root)

        self.assertEqual(status, 1)
        self.assertIn("stale", output)

    @canonical_only
    def test_annotating_removes_the_occurrence_from_the_ratchet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            write(root, self.VIEW, "def f():\n    return Thing._base_manager.all()  # unscoped: reviewed\n")

            self.assertEqual(self.run_main(root)[0], 1)
            self.assertEqual(self.run_main(root, "--write-baseline")[0], 0)
            status, output = self.run_main(root)

        self.assertEqual(status, 0)
        self.assertIn("1 justified in place (unscoped=1)", output)

    @canonical_only
    def test_write_baseline_refuses_new_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            write(
                root,
                self.VIEW,
                "def f():\n    return Thing._base_manager.all()\n\ndef g():\n    return X.all_objects\n",
            )
            status, _ = self.run_main(root, "--write-baseline")
            baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))

        self.assertEqual(status, 1)
        self.assertEqual(len(baseline["findings"]), 1)

    @canonical_only
    def test_malformed_annotation_fails_and_blocks_baseline_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, self.VIEW, "def f():\n    return Thing._base_manager.all()  # unscoped:\n")
            status, output = self.run_main(root, "--write-baseline")

        self.assertEqual(status, 1)
        self.assertIn("unusable justification", output)

    @canonical_only
    def test_unreadable_baseline_is_exit_code_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, self.VIEW, "x = 1\n")
            status, output = self.run_main(root)

        self.assertEqual(status, 2)
        self.assertIn("cannot read baseline", output)

    def test_non_canonical_interpreter_is_refused(self):
        from unittest import mock

        with mock.patch.object(sys, "version_info", (3, 11, 0)), redirect_stderr(io.StringIO()) as stderr:
            status = main([])

        self.assertEqual(status, 2)
        self.assertIn("Refusing", stderr.getvalue())


class RepositoryPolicyTests(unittest.TestCase):
    def test_every_rule_is_documented_in_agents_md(self):
        text = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for rule in RULES:
            self.assertIn(f"`{rule}`", text)
        self.assertIn("scripts/check_escape_hatches.py", text)

    def test_gate_is_wired_into_ci_and_pre_commit(self):
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        config = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        self.assertIn("python scripts/check_escape_hatches.py", workflow)
        self.assertIn("entry: python scripts/check_escape_hatches.py", config)
        self.assertNotIn("--write-baseline", workflow)

    @canonical_only
    def test_checked_in_baseline_matches_the_repository(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stdout):
            status = main([])

        self.assertEqual(status, 0, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
