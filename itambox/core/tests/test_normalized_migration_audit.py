import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.migration_audit as migration_audit
from scripts.migration_audit import (
    NORMALIZED_POST_TRANSITION_DISPOSITIONS,
    POST_TRANSITION_DISPOSITION_GROUPS,
    POST_TRANSITION_MIGRATIONS,
    TRUSTED_PRE_CLEANUP_REVISION,
    _load_trusted_normalized_evidence,
    _normalized_post_transition_dispositions,
    _strict_migration_operations,
    build_inventory,
    main,
    validate_preflight_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class NormalizedMigrationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository_root = REPOSITORY_ROOT
        cls.authoritative_directory = tempfile.TemporaryDirectory()
        cls.authoritative_root = Path(cls.authoritative_directory.name) / "fixture"
        cls.trusted = _load_trusted_normalized_evidence(str(cls.repository_root))

        for migration_id in cls.trusted["baseline_ids"]:
            app, name = migration_id.split(".", 1)
            source = cls._git_show(f"itambox/{app}/migrations/{name}.py")
            cls._write_migration(cls.authoritative_root, app, name, cls._without_replaces(source))
        current_by_id = {
            f"{path.parent.parent.name}.{path.stem}": path
            for path in (cls.repository_root / "itambox").glob("*/migrations/[0-9]*.py")
        }
        missing = POST_TRANSITION_MIGRATIONS - set(current_by_id)
        if missing:
            raise AssertionError(f"authoritative fixture cannot find post-transition migrations: {sorted(missing)}")
        for migration_id in sorted(POST_TRANSITION_MIGRATIONS):
            app, name = migration_id.split(".", 1)
            cls._write_migration(
                cls.authoritative_root,
                app,
                name,
                current_by_id[migration_id].read_text(encoding="utf-8"),
            )

        manifest = json.loads(
            (cls.repository_root / "itambox/core/migration_baseline_manifest.json").read_text(encoding="utf-8")
        )
        cls.contract_template = {
            "mode": "normalized",
            "fixture_only": True,
            "trusted_pre_cleanup_revision": TRUSTED_PRE_CLEANUP_REVISION,
            "first_party_apps": cls.trusted["first_party_apps"],
            "baseline_ids": cls.trusted["baseline_ids"],
            "deleted_historical_ids": cls.trusted["deleted_historical_ids"],
            "root_ids": cls.trusted["root_ids"],
            "baseline_leaf_ids": cls.trusted["baseline_leaf_ids"],
            "post_transition_leaf_ids": manifest["post_transition_leaf_ids"],
            "special_users_bootstrap": "users.0000_issue88_shard_01_users_bootstrap",
        }

    @classmethod
    def tearDownClass(cls):
        cls.authoritative_directory.cleanup()

    @classmethod
    def _git_show(cls, path):
        result = subprocess.run(
            ["git", "-C", str(cls.repository_root), "show", f"{TRUSTED_PRE_CLEANUP_REVISION}:{path}"],
            check=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout.decode("utf-8")

    @staticmethod
    def _without_replaces(source):
        tree = ast.parse(source)
        migration_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
        )
        removed = [
            node
            for node in migration_class.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in {"replaces", "run_before"} for target in node.targets)
        ]
        lines = source.splitlines(keepends=True)
        for declaration in sorted(removed, key=lambda node: node.lineno, reverse=True):
            del lines[declaration.lineno - 1 : declaration.end_lineno]
        return "".join(lines)

    @staticmethod
    def _write_migration(root, app, name, source):
        directory = root / app / "migrations"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.py"
        path.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
        return path

    def _fixture(self):
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name) / "fixture"
        shutil.copytree(self.authoritative_root, root)
        contract = json.loads(json.dumps(self.contract_template))
        return temporary_directory, root, contract

    def _path(self, root, migration_id):
        app, name = migration_id.split(".", 1)
        return root / app / "migrations" / f"{name}.py"

    def _baseline_path(self, root, ordinal=1):
        return self._path(root, self.trusted["baseline_ids"][ordinal - 1])

    @staticmethod
    def _runtime_manifest(contract):
        post_transition_ids = sorted(POST_TRANSITION_MIGRATIONS)
        return {
            "schema_version": 1,
            "layout": "normalized",
            "first_party_apps": contract["first_party_apps"],
            "historical_ids": sorted(contract["deleted_historical_ids"]),
            "replacement_ids": sorted(contract["baseline_ids"]),
            "replacement_target_ids": sorted(contract["deleted_historical_ids"]),
            "baseline_ids": sorted(contract["baseline_ids"]),
            "post_transition_ids": post_transition_ids,
            "post_transition_leaf_ids": sorted(contract["post_transition_leaf_ids"]),
            "current_leaf_ids": sorted(contract["post_transition_leaf_ids"]),
            "transition_release_sha": TRUSTED_PRE_CLEANUP_REVISION,
            "supported_predecessors": [
                {
                    "name": "issue88-transition-release",
                    "revision": TRUSTED_PRE_CLEANUP_REVISION,
                    "state": "complete-replacement-recognition",
                }
            ],
        }

    def _build(self, root, contract, **kwargs):
        parameters = {
            "layout": "normalized",
            "normalized_contract": contract,
            "repository_root": self.repository_root,
        }
        parameters.update(kwargs)
        return build_inventory(root, **parameters)

    def _assert_rejected(self, root, contract, pattern, **kwargs):
        with self.assertRaisesRegex(ValueError, pattern):
            self._build(root, contract, **kwargs)

    def _replace(self, path, old, new):
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_accepts_full_authoritative_normalized_fixture(self):
        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            inventory = self._build(root, contract)
        self.assertEqual(len(inventory["normalized_contract"]["baseline_ids"]), 62)
        self.assertEqual(set(inventory["post_transition_migrations"]), POST_TRANSITION_MIGRATIONS)
        self.assertEqual(
            set(inventory["normalized_contract"]["post_transition_dispositions"]),
            POST_TRANSITION_MIGRATIONS,
        )

    def test_semantic_evidence_allows_formatting_but_not_self_authorized_change(self):
        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root, 2)
        with temporary_directory:
            path.write_text("# harmless formatting change\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            self._build(root, contract)

        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root, 2)
        with temporary_directory:
            self._replace(path, "operations = [", 'operations = [migrations.RunSQL("SELECT 1"),')
            self._assert_rejected(root, contract, "whole-module semantics disagree with trusted pre-cleanup")

        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            contract["baseline_source_fingerprints"] = {
                migration_id: "0" * 64 for migration_id in contract["baseline_ids"]
            }
            self._assert_rejected(root, contract, "unsupported self-authorizing fields")

    def test_contract_identity_and_order_are_bound_to_trusted_evidence(self):
        mutations = (
            ("baseline_ids", lambda value: list(reversed(value))),
            ("deleted_historical_ids", lambda value: value[1:]),
            ("root_ids", lambda value: value + [value[0] + "_renamed"]),
            ("baseline_leaf_ids", lambda value: [self.trusted["baseline_ids"][-2]]),
            ("first_party_apps", lambda value: value[:-1]),
        )
        for field, mutate in mutations:
            with self.subTest(field=field):
                temporary_directory, root, contract = self._fixture()
                with temporary_directory:
                    contract[field] = mutate(contract[field])
                    self._assert_rejected(root, contract, "trusted pre-cleanup evidence")

    def test_rejects_untrusted_revision(self):
        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            contract["trusted_pre_cleanup_revision"] = "0" * 40
            self._assert_rejected(root, contract, "must name the trusted pre-cleanup revision")

    def test_post_transition_disposition_is_exhaustive_and_unknown_ids_fail_closed(self):
        self.assertEqual(set(POST_TRANSITION_DISPOSITION_GROUPS), NORMALIZED_POST_TRANSITION_DISPOSITIONS)
        self.assertEqual(NORMALIZED_POST_TRANSITION_DISPOSITIONS, {"RETAIN"})
        self.assertEqual(set(POST_TRANSITION_DISPOSITION_GROUPS["RETAIN"]), POST_TRANSITION_MIGRATIONS)
        disposition_sets = list(POST_TRANSITION_DISPOSITION_GROUPS.values())
        self.assertEqual(set().union(*disposition_sets), POST_TRANSITION_MIGRATIONS)
        for index, migration_ids in enumerate(disposition_sets):
            for other_ids in disposition_sets[index + 1 :]:
                self.assertTrue(migration_ids.isdisjoint(other_ids))

        temporary_directory, root, contract = self._fixture()
        missing_id = sorted(POST_TRANSITION_MIGRATIONS)[0]
        with temporary_directory:
            self._path(root, missing_id).unlink()
            self._assert_rejected(root, contract, "post-transition IDs must exactly match")

        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            self._write_migration(
                root,
                "extras",
                "9999_unknown",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = []
                    operations = []
                """,
            )
            self._assert_rejected(root, contract, "unknown=.*extras.9999_unknown")

        with (
            patch.dict(
                POST_TRANSITION_DISPOSITION_GROUPS,
                {"RETAIN": POST_TRANSITION_MIGRATIONS, "FOLD_INTO_BASELINE": frozenset()},
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "unknown or missing groups"),
        ):
            _normalized_post_transition_dispositions()

        retained_subset = frozenset(set(POST_TRANSITION_MIGRATIONS) - {sorted(POST_TRANSITION_MIGRATIONS)[0]})
        with (
            patch.dict(
                POST_TRANSITION_DISPOSITION_GROUPS,
                {"RETAIN": retained_subset},
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "cover exactly"),
        ):
            _normalized_post_transition_dispositions()

    def test_rejects_non_direct_wrong_decorated_and_keyword_migration_classes(self):
        cases = {
            "nested": (
                "class Migration(migrations.Migration):",
                "if True:\n    class Migration(migrations.Migration):",
                "module-level statement is not allowlisted",
            ),
            "wrong-base": (
                "class Migration(migrations.Migration):",
                "class Migration(object):",
                "directly inherit only migrations.Migration",
            ),
            "extra-base": (
                "class Migration(migrations.Migration):",
                "class Migration(migrations.Migration, object):",
                "directly inherit only migrations.Migration",
            ),
            "decorated": (
                "class Migration(migrations.Migration):",
                "@decorator\nclass Migration(migrations.Migration):",
                "must be undecorated",
            ),
            "metaclass": (
                "class Migration(migrations.Migration):",
                "class Migration(migrations.Migration, metaclass=type):",
                "must not use metaclass or class keywords",
            ),
        }
        for name, (old, new, pattern) in cases.items():
            with self.subTest(name=name):
                temporary_directory, root, contract = self._fixture()
                with temporary_directory:
                    path = self._baseline_path(root)
                    if name == "nested":
                        path.write_text(
                            "from django.db import migrations\n\n"
                            "if True:\n"
                            "    class Migration(migrations.Migration):\n"
                            "        dependencies = []\n"
                            "        operations = []\n",
                            encoding="utf-8",
                        )
                    else:
                        self._replace(path, old, new)
                    self._assert_rejected(root, contract, pattern)

    def test_rejects_duplicate_migration_class(self):
        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root)
        with temporary_directory:
            path.write_text(
                path.read_text(encoding="utf-8") + "\nclass Migration(migrations.Migration):\n    pass\n",
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "exactly one direct Migration class")

    def test_rejects_all_non_allowlisted_module_level_mutation_and_execution(self):
        additions = {
            "reassign": "Migration.operations = []",
            "delete": "del Migration.operations",
            "setattr": "setattr(Migration, 'operations', [])",
            "delattr": "delattr(Migration, 'operations')",
            "dynamic-setattr": "setattr(Migration, field_name(), [])",
            "class-reassign": "Migration = object",
            "class-delete": "del Migration",
            "exec": "exec('Migration.operations = []')",
            "aliased-setattr": "cls = Migration\nsetattr(cls, 'opera' + 'tions', [])",
            "qualified-exec": "import builtins\nbuiltins.exec('Migration.operations = []')",
            "type-setattr": "type.__setattr__(Migration, 'operations', [])",
        }
        for name, addition in additions.items():
            with self.subTest(name=name):
                temporary_directory, root, contract = self._fixture()
                path = self._baseline_path(root)
                with temporary_directory:
                    path.write_text(path.read_text(encoding="utf-8") + f"\n{addition}\n", encoding="utf-8")
                    self._assert_rejected(root, contract, "normalized")

    def test_rejects_duplicate_annotated_augassign_namedexpr_and_nested_fields(self):
        cases = {
            "duplicate": "    dependencies = []\n",
            "annotated": "    operations: list = []\n",
            "augassign": "    operations += []\n",
            "namedexpr": "    marker = (operations := [])\n",
            "nested": "    if True:\n        operations = []\n",
            "tuple": "    operations, marker = [], None\n",
        }
        for name, insertion in cases.items():
            with self.subTest(name=name):
                temporary_directory, root, contract = self._fixture()
                path = self._baseline_path(root)
                with temporary_directory:
                    self._replace(path, "    operations = [", insertion + "    operations = [")
                    self._assert_rejected(root, contract, "normalized")

    def test_rejects_missing_required_reserved_field(self):
        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root)
        with temporary_directory:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            migration_class = next(
                node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
            )
            dependencies = next(
                node
                for node in migration_class.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "dependencies" for target in node.targets)
            )
            lines = source.splitlines(keepends=True)
            del lines[dependencies.lineno - 1 : dependencies.end_lineno]
            path.write_text("".join(lines), encoding="utf-8")
            self._assert_rejected(root, contract, "must directly declare dependencies")

    def test_strict_operations_reject_dynamic_args_kwargs_and_separate_state_forms(self):
        rejected = {
            "factory": "[build_operation()]",
            "star": "[migrations.RunSQL(*arguments)]",
            "kwargs": "[migrations.RunSQL(**arguments)]",
            "separate-dynamic-positional": "[migrations.SeparateDatabaseAndState(build_operations())]",
            "separate-dynamic-keyword": (
                "[migrations.SeparateDatabaseAndState(database_operations=build_operations())]"
            ),
            "separate-too-many": "[migrations.SeparateDatabaseAndState([], [], [])]",
            "separate-unknown": "[migrations.SeparateDatabaseAndState(other_operations=[])]",
            "separate-kwargs": "[migrations.SeparateDatabaseAndState(**arguments)]",
        }
        for name, source in rejected.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "normalized"):
                _strict_migration_operations(ast.parse(source, mode="eval").body)

    def test_strict_operations_accept_and_represent_separate_state_positional_and_keywords(self):
        positional = _strict_migration_operations(
            ast.parse(
                "[migrations.SeparateDatabaseAndState([migrations.RunSQL('SELECT 1')], [])]",
                mode="eval",
            ).body
        )
        keyword = _strict_migration_operations(
            ast.parse(
                "[migrations.SeparateDatabaseAndState(state_operations=[migrations.AddField("
                "model_name='asset', name='serial', field=models.CharField())])]",
                mode="eval",
            ).body
        )
        self.assertEqual(positional[0]["nested"]["database_operations"][0]["name"], "RunSQL")
        self.assertEqual(keyword[0]["nested"]["state_operations"][0]["name"], "AddField")
        self.assertEqual(keyword[0]["nested"]["state_operations"][0]["kwargs"][0][0], "model_name")

    def test_operation_args_and_kwargs_are_part_of_trusted_semantics(self):
        temporary_directory, root, contract = self._fixture()
        candidate = next(
            self._path(root, migration_id)
            for migration_id in self.trusted["baseline_ids"]
            if "migrations.RunPython(" in self._path(root, migration_id).read_text(encoding="utf-8")
        )
        with temporary_directory:
            self._replace(candidate, "migrations.RunPython(", "migrations.RunPython(migrations.RunPython.noop, ")
            self._assert_rejected(root, contract, "whole-module semantics disagree with trusted pre-cleanup")

    def test_whole_module_semantics_bind_imports_helpers_and_custom_operation_bodies(self):
        temporary_directory, root, contract = self._fixture()
        helper_path = self._path(root, "assets.0100_issue88_shard_43_assets_seed")
        with temporary_directory:
            self._replace(
                helper_path,
                "def seed_required_asset_data(apps, schema_editor):",
                "def seed_required_asset_data(apps, schema_editor):\n    raise RuntimeError('changed helper')",
            )
            self._assert_rejected(root, contract, "whole-module semantics disagree with trusted pre-cleanup")

        temporary_directory, root, contract = self._fixture()
        import_path = self._baseline_path(root)
        with temporary_directory:
            self._replace(import_path, "import core.mixins", "import core.models as core_mixins")
            self._assert_rejected(root, contract, "whole-module semantics disagree with trusted pre-cleanup")

        temporary_directory, root, contract = self._fixture()
        operation_path = self._path(root, "extras.0105_reporttemplate_advanced_mode_and_more")
        with temporary_directory:
            self._replace(
                operation_path,
                "class AddPersistentReportDesignerFields(Operation):",
                "class AddPersistentReportDesignerFields(Operation):\n    changed_contract = True",
            )
            self._assert_rejected(root, contract, "retained post-transition whole-module semantics")

    def test_retained_post_transition_semantics_must_match_trusted_source(self):
        temporary_directory, root, contract = self._fixture()
        path = self._path(root, "compliance.0101_alter_custodyreceipt_signed_at")
        with temporary_directory:
            self._replace(path, "verbose_name='Signed At'", "verbose_name='Mutated Signed At'")
            self._assert_rejected(root, contract, "retained post-transition whole-module semantics")

    def test_rejects_deleted_history_dependency_and_run_before(self):
        deleted_id = self.contract_template["deleted_historical_ids"][0]
        deleted_app, deleted_name = deleted_id.split(".", 1)
        for field in ("dependencies", "run_before"):
            with self.subTest(field=field):
                temporary_directory, root, contract = self._fixture()
                path = self._path(root, sorted(POST_TRANSITION_MIGRATIONS)[0])
                with temporary_directory:
                    if field == "dependencies":
                        source = path.read_text(encoding="utf-8")
                        tree = ast.parse(source)
                        migration_class = next(
                            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
                        )
                        declaration = next(
                            node
                            for node in migration_class.body
                            if isinstance(node, ast.Assign)
                            and any(
                                isinstance(target, ast.Name) and target.id == "dependencies" for target in node.targets
                            )
                        )
                        lines = source.splitlines(keepends=True)
                        lines[declaration.lineno - 1 : declaration.end_lineno] = [
                            f"    dependencies = [({deleted_app!r}, {deleted_name!r})]\n"
                        ]
                        path.write_text("".join(lines), encoding="utf-8")
                    else:
                        self._replace(
                            path,
                            "    operations = [",
                            f"    run_before = [({deleted_app!r}, {deleted_name!r})]\n    operations = [",
                        )
                    self._assert_rejected(root, contract, "references deleted historical migration")

    def test_rejects_source_symlink_escape_and_non_numeric_module(self):
        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root)
        with temporary_directory:
            outside = Path(temporary_directory.name) / "outside.py"
            outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.unlink()
            try:
                os.symlink(outside, path)
            except OSError:
                if os.name != "nt":
                    self.fail("the platform rejected creation of the containment-test symlink")
                outside_migrations = Path(temporary_directory.name) / "outside-migrations"
                outside_migrations.mkdir()
                shutil.copyfile(outside, outside_migrations / "9999_escape.py")
                escape_app = root / "escape"
                escape_app.mkdir()
                junction = escape_app / "migrations"
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(junction), str(outside_migrations)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.assertEqual(result.returncode, 0, "unable to create a Windows junction for containment test")
                try:
                    self._assert_rejected(root, contract, "escapes the fixture root")
                finally:
                    os.rmdir(junction)
            else:
                self._assert_rejected(root, contract, "escapes the fixture root")

        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            self._write_migration(root, "extras", "helper", "value = 1")
            self._assert_rejected(root, contract, "must have a numeric prefix")

    def test_allowlist_rejects_post_declaration_runtime_mutation(self):
        temporary_directory, root, contract = self._fixture()
        path = self._baseline_path(root)
        with temporary_directory:
            path.write_text(
                path.read_text(encoding="utf-8") + "\ngetattr(Migration, 'operations').clear()\n",
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "module-level statement is not allowlisted")

    def test_runtime_parity_is_value_bearing(self):
        temporary_directory, root, contract = self._fixture()
        real_run = subprocess.run

        def mutate_runtime_fingerprint(*args, **kwargs):
            result = real_run(*args, **kwargs)
            command = args[0]
            if "-c" not in command or migration_audit._RUNTIME_INSPECTOR not in command:
                return result
            output = json.loads(result.stdout)
            metadata = next(item for item in output.values() if item["operations"])
            metadata["operations"][0]["value_fingerprint"] = "0" * 64
            return subprocess.CompletedProcess(
                result.args,
                result.returncode,
                stdout=json.dumps(output),
                stderr=result.stderr,
            )

        with (
            temporary_directory,
            patch("scripts.migration_audit.subprocess.run", side_effect=mutate_runtime_fingerprint),
        ):
            self._assert_rejected(root, contract, "runtime operation values disagree")

    def test_isolated_runtime_import_failure_is_closed(self):
        temporary_directory, root, contract = self._fixture()
        failure = subprocess.CompletedProcess(
            ["python"],
            1,
            stdout="",
            stderr="RuntimeError: fixture import failed\n",
        )
        with (
            temporary_directory,
            patch(
                "scripts.migration_audit.subprocess.run",
                return_value=failure,
            ),
        ):
            self._assert_rejected(
                root,
                contract,
                "isolated runtime import failed: RuntimeError: fixture import failed",
            )

    def test_isolated_runtime_timeout_and_process_failures_are_clean(self):
        for error, pattern in (
            (subprocess.TimeoutExpired(["python"], 120), "runtime import timed out"),
            (OSError("cannot spawn"), "runtime process failed"),
        ):
            with self.subTest(pattern=pattern):
                temporary_directory, root, contract = self._fixture()
                with temporary_directory, patch("scripts.migration_audit.subprocess.run", side_effect=error):
                    self._assert_rejected(root, contract, pattern)

    def test_isolated_runtime_uses_minimal_environment(self):
        temporary_directory, root, contract = self._fixture()
        real_run = subprocess.run
        observed_environment = None

        def record_environment(*args, **kwargs):
            nonlocal observed_environment
            command = args[0]
            if "-c" in command and migration_audit._RUNTIME_INSPECTOR in command:
                observed_environment = kwargs["env"]
            return real_run(*args, **kwargs)

        with temporary_directory, patch("scripts.migration_audit.subprocess.run", side_effect=record_environment):
            self._build(root, contract)
        expected_keys = {"ITAMBOX_ENV", "DJANGO_SETTINGS_MODULE"} | {
            key for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ
        }
        self.assertEqual(set(observed_environment), expected_keys)

    def test_normalized_manifest_validation_uses_legacy_recognition_ids(self):
        temporary_directory, root, contract = self._fixture()
        with temporary_directory:
            inventory = self._build(root, contract)
            manifest = self._runtime_manifest(contract)
            self.assertIs(validate_preflight_manifest(inventory, manifest), manifest)

    def test_normalized_cli_requires_explicit_external_paths(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--layout", "normalized"])
        self.assertEqual(raised.exception.code, 2)

    def test_normalized_cli_writes_only_external_output(self):
        temporary_directory, root, contract = self._fixture()
        base = Path(temporary_directory.name)
        contract_path = base / "contract.json"
        manifest_path = base / "manifest.json"
        output_path = base / "audit.json"
        runtime_manifest = self._runtime_manifest(contract)

        def load_external_fixture(path):
            if path == contract_path:
                return contract
            if path == manifest_path:
                return runtime_manifest
            self.fail(f"unexpected normalized CLI fixture input: {path}")

        live_output = self.repository_root / "scripts/migration_audit.json"
        before = live_output.read_bytes()
        with (
            temporary_directory,
            contextlib.redirect_stdout(io.StringIO()),
            patch("scripts.migration_audit.load_preflight_manifest", side_effect=load_external_fixture),
        ):
            result = main(
                [
                    "--layout",
                    "normalized",
                    "--source-root",
                    str(root),
                    "--normalized-contract",
                    str(contract_path),
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(output_path),
                ]
            )
            rendered = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(rendered["layout"], "normalized")
        self.assertFalse(contract_path.exists())
        self.assertFalse(manifest_path.exists())
        self.assertEqual(live_output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
