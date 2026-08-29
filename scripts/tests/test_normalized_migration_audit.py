import contextlib
import hashlib
import io
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.migration_audit import CHECKED_HISTORICAL_IDS, build_inventory, main, validate_preflight_manifest


class NormalizedMigrationAuditTests(unittest.TestCase):
    def _fixture(self):
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name) / "fixture"
        bootstrap = self._write_migration(
            root,
            "users",
            "0000_issue88_shard_01_users_bootstrap",
            """
            from django.conf import settings
            from django.db import migrations

            class Migration(migrations.Migration):
                dependencies = []
                operations = []
            """,
        )
        baseline = self._write_migration(
            root,
            "extras",
            "0100_issue88_shard_02_extras_schema",
            """
            from django.db import migrations

            class Migration(migrations.Migration):
                dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]
                operations = []
            """,
        )
        post_transition = self._write_migration(
            root,
            "extras",
            "0102_alter_event_action",
            """
            from django.db import migrations

            class Migration(migrations.Migration):
                dependencies = [("extras", "0100_issue88_shard_02_extras_schema")]
                operations = []
            """,
        )
        baseline_ids = [
            "users.0000_issue88_shard_01_users_bootstrap",
            "extras.0100_issue88_shard_02_extras_schema",
        ]
        contract = {
            "mode": "normalized",
            "fixture_only": True,
            "first_party_apps": ["extras", "users"],
            "baseline_ids": baseline_ids,
            "baseline_source_fingerprints": {
                baseline_ids[0]: hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
                baseline_ids[1]: hashlib.sha256(baseline.read_bytes()).hexdigest(),
            },
            "deleted_historical_ids": sorted(CHECKED_HISTORICAL_IDS),
            "root_ids": [baseline_ids[0]],
            "baseline_leaf_ids": [baseline_ids[1]],
            "post_transition_leaf_ids": ["extras.0102_alter_event_action"],
            "special_users_bootstrap": baseline_ids[0],
        }
        return temporary_directory, root, contract, bootstrap, baseline, post_transition

    @staticmethod
    def _write_migration(root, app, name, source):
        directory = root / app / "migrations"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.py"
        path.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _refresh(contract, migration_id, path):
        contract["baseline_source_fingerprints"][migration_id] = hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _runtime_manifest(contract):
        post_transition_ids = contract["post_transition_leaf_ids"]
        return {
            "schema_version": 1,
            "layout": "normalized",
            "first_party_apps": ["extras", "users"],
            "historical_ids": sorted(contract["deleted_historical_ids"]),
            "replacement_ids": sorted(contract["baseline_ids"]),
            "replacement_target_ids": sorted(contract["deleted_historical_ids"]),
            "baseline_ids": sorted(contract["baseline_ids"]),
            "post_transition_ids": sorted(post_transition_ids),
            "post_transition_leaf_ids": sorted(post_transition_ids),
            "current_leaf_ids": sorted(post_transition_ids),
            "transition_release_sha": "b" * 40,
            "supported_predecessors": [
                {
                    "name": "test-predecessor",
                    "revision": "b" * 40,
                    "state": "complete-replacement-recognition",
                }
            ],
        }

    def _assert_rejected(self, root, contract, pattern, **kwargs):
        parameters = {
            "semantic_dispositions": {},
            "expected_blockers": [],
            "layout": "normalized",
            "normalized_contract": contract,
        }
        parameters.update(kwargs)
        with self.assertRaisesRegex(ValueError, pattern):
            build_inventory(root, **parameters)

    def test_accepts_explicit_normalized_contract(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            inventory = build_inventory(
                root,
                semantic_dispositions={},
                expected_blockers=[],
                layout="normalized",
                normalized_contract=contract,
            )
        self.assertEqual(inventory["layout"], "normalized")
        self.assertEqual(inventory["normalized_contract"]["baseline_ids"], contract["baseline_ids"])

    def test_default_policy_accepts_deleted_historical_blockers(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            inventory = build_inventory(root, layout="normalized", normalized_contract=contract)
        self.assertEqual(inventory["reviewed_semantics"]["blockers"], [])

    def test_normalized_contract_requires_fixture_only(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract.pop("fixture_only")
            self._assert_rejected(root, contract, "must be explicitly fixture_only")

    def test_normalized_cli_requires_explicit_fixture_root_and_contract(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--layout", "normalized"])
        self.assertEqual(raised.exception.code, 2)

    def test_normalized_cli_requires_explicit_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--layout",
                        "normalized",
                        "--source-root",
                        temporary_directory,
                        "--normalized-contract",
                        str(Path(temporary_directory) / "contract.json"),
                    ]
                )
        self.assertEqual(raised.exception.code, 2)

    def test_normalized_cli_uses_external_manifest_and_output(self):
        temporary_directory, root, contract, *_ = self._fixture()
        base = Path(temporary_directory.name)
        contract_path = base / "normalized-contract.json"
        manifest_path = base / "normalized-manifest.json"
        output_path = base / "normalized-audit.json"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        manifest_path.write_text(json.dumps(self._runtime_manifest(contract)), encoding="utf-8")
        live_output = Path(__file__).resolve().parents[2] / "scripts" / "migration_audit.json"
        live_before = live_output.read_text(encoding="utf-8")
        with temporary_directory, contextlib.redirect_stdout(io.StringIO()):
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
            output_layout = json.loads(output_path.read_text(encoding="utf-8"))["layout"]
            live_after = live_output.read_text(encoding="utf-8")
        self.assertEqual(result, 0)
        self.assertEqual(output_layout, "normalized")
        self.assertEqual(live_after, live_before)

    def test_normalized_cli_rejects_live_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--layout",
                        "normalized",
                        "--source-root",
                        temporary_directory,
                        "--normalized-contract",
                        str(Path(temporary_directory) / "contract.json"),
                        "--manifest",
                        str(Path(temporary_directory) / "manifest.json"),
                        "--output",
                        str(Path(__file__).resolve().parents[2] / "scripts" / "migration_audit.json"),
                    ]
                )
        self.assertEqual(raised.exception.code, 2)

    def test_normalized_cli_rejects_repository_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(__file__).resolve().parents[2]
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--layout",
                        "normalized",
                        "--source-root",
                        temporary_directory,
                        "--normalized-contract",
                        str(repository_root / "scripts" / "migration_audit.json"),
                        "--manifest",
                        str(repository_root / "scripts" / "migration_audit.json"),
                        "--output",
                        str(Path(temporary_directory) / "normalized-audit.json"),
                    ]
                )
        self.assertEqual(raised.exception.code, 2)

    def test_transitional_cli_rejects_a_normalized_contract(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--normalized-contract", "unused.json"])
        self.assertEqual(raised.exception.code, 2)

    def test_normalized_manifest_validation_uses_legacy_recognition_ids(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            inventory = build_inventory(
                root,
                semantic_dispositions={},
                expected_blockers=[],
                layout="normalized",
                normalized_contract=contract,
            )
            manifest = {
                "schema_version": 1,
                "layout": "normalized",
                "first_party_apps": ["extras", "users"],
                "historical_ids": sorted(contract["deleted_historical_ids"]),
                "replacement_ids": sorted(contract["baseline_ids"]),
                "replacement_target_ids": sorted(contract["deleted_historical_ids"]),
                "baseline_ids": sorted(contract["baseline_ids"]),
                "post_transition_ids": ["extras.0102_alter_event_action"],
                "post_transition_leaf_ids": sorted(contract["post_transition_leaf_ids"]),
                "current_leaf_ids": sorted(contract["post_transition_leaf_ids"]),
                "transition_release_sha": "b" * 40,
                "supported_predecessors": [
                    {
                        "name": "test-predecessor",
                        "revision": "b" * 40,
                        "state": "complete-replacement-recognition",
                    }
                ],
            }
            self.assertIs(validate_preflight_manifest(inventory, manifest), manifest)

    def test_rejects_annotated_dependency_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    'dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                    'dependencies: list = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must not use an annotated assignment")

    def test_rejects_duplicate_dependency_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    'dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                    'dependencies = []\n    dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "has duplicate assignments")

    def test_rejects_nested_dependency_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "    operations = []",
                    '    if True:\n        dependencies = [("users", "0001_initial")]\n    operations = []',
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must use a direct class assignment")

    def test_rejects_nonliteral_dependency_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    '"0000_issue88_shard_01_users_bootstrap"',
                    '"0000_issue88_shard_01_users_" + "bootstrap"',
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "contains a non-literal reference")

    def test_rejects_nonliteral_run_before_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "    operations = []",
                    "    run_before = [build_target()]\n    operations = []",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "run_before contains an unparsed reference")

    def test_rejects_nonliteral_replaces_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "    operations = []",
                    "    replaces = [build_target()]\n    operations = []",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "replaces contains an unparsed reference")

    def test_rejects_nonliteral_operations_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = operations_factory()",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "operations must be a literal list or tuple")

    def test_rejects_noncall_operation_assignment(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = [None]",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "operations contains an unparsed operation")

    def test_rejects_duplicate_migration_classes(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8") + "\nclass Migration: pass\n",
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must define exactly one Migration class")

    def test_rejects_dynamic_swappable_dependency(self):
        temporary_directory, root, contract, bootstrap, _baseline, _post = self._fixture()
        with temporary_directory:
            bootstrap.write_text(
                bootstrap.read_text(encoding="utf-8").replace(
                    "dependencies = []",
                    "dependencies = [migrations.swappable_dependency(get_user_model())]",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "users.0000_issue88_shard_01_users_bootstrap", bootstrap)
            self._assert_rejected(root, contract, "dependencies contains an unparsed reference")

    def test_rejects_operation_factory(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = [build_operation()]",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "operations contains an unparsed operation")

    def test_rejects_augmented_reserved_field_binding(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "    operations = []",
                    "    operations = []\n    dependencies += []",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must use a direct class assignment")

    def test_rejects_non_numeric_importable_migration_module(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            self._write_migration(root, "extras", "helper", "")
            self._assert_rejected(root, contract, "must have a numeric prefix")

    def test_rejects_tuple_reserved_field_binding(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    'dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                    "dependencies, other = [], 1",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must use a direct class assignment")

    def test_rejects_named_expression_reserved_field_binding(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = [(dependencies := [])]",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "must use a direct class assignment")

    def test_rejects_dynamic_separate_database_operations(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = [migrations.SeparateDatabaseAndState(database_operations=build_operations())]",
                ),
                encoding="utf-8",
            )
            self._assert_rejected(root, contract, "operations must be a literal list or tuple")

    def test_allows_literal_swappable_dependency(self):
        temporary_directory, root, contract, bootstrap, _baseline, _post = self._fixture()
        with temporary_directory:
            bootstrap.write_text(
                bootstrap.read_text(encoding="utf-8").replace(
                    "dependencies = []",
                    "dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "users.0000_issue88_shard_01_users_bootstrap", bootstrap)
            inventory = build_inventory(
                root,
                semantic_dispositions={},
                expected_blockers=[],
                layout="normalized",
                normalized_contract=contract,
            )
        self.assertEqual(inventory["layout"], "normalized")

    def test_rejects_migration_symlink_outside_fixture_root(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            outside = Path(temporary_directory.name) / "outside-migration.py"
            outside.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
            baseline.unlink()
            try:
                os.symlink(outside, baseline)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            self._assert_rejected(root, contract, "escapes the fixture root")

    def test_rejects_incomplete_checked_historical_set(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["deleted_historical_ids"] = contract["deleted_historical_ids"][1:]
            self._assert_rejected(
                root,
                contract,
                "must match the complete checked historical ID set",
            )

    def test_rejects_missing_or_renamed_baseline(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.unlink()
            self._assert_rejected(root, contract, "normalized baseline IDs")

        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.rename(baseline.with_name("0100_issue88_shard_02_extras_renamed.py"))
            self._assert_rejected(root, contract, "normalized baseline IDs")

    def test_rejects_noncontiguous_shard_ordinals(self):
        temporary_directory, root, contract, _bootstrap, baseline, post_transition = self._fixture()
        with temporary_directory:
            old_id = "extras.0100_issue88_shard_02_extras_schema"
            new_id = "extras.0100_issue88_shard_03_extras_schema"
            new_path = baseline.with_name("0100_issue88_shard_03_extras_schema.py")
            baseline.rename(new_path)
            post_transition.write_text(
                post_transition.read_text(encoding="utf-8").replace(old_id.split(".", 1)[1], new_id.split(".", 1)[1]),
                encoding="utf-8",
            )
            contract["baseline_ids"][1] = new_id
            contract["baseline_source_fingerprints"][new_id] = contract["baseline_source_fingerprints"].pop(old_id)
            contract["baseline_leaf_ids"] = [new_id]
            self._refresh(contract, new_id, new_path)
            self._assert_rejected(root, contract, "shard ordinals must be contiguous")

    def test_rejects_reordered_baseline_contract(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["baseline_ids"] = list(reversed(contract["baseline_ids"]))
            self._assert_rejected(root, contract, r"normalized baseline (?:order/dependency mismatch|shard ordinals)")

    def test_rejects_baseline_source_fingerprint_mutation(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(baseline.read_text(encoding="utf-8") + "# mutation\n", encoding="utf-8")
            self._assert_rejected(root, contract, "normalized baseline source fingerprint mismatch")

    def test_rejects_incomplete_fingerprint_map(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["baseline_source_fingerprints"].pop(contract["baseline_ids"][0])
            self._assert_rejected(root, contract, "fingerprints must cover exactly baseline_ids")

    def test_rejects_dependency_on_deleted_historical_migration(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                textwrap.dedent(
                    """
                    from django.db import migrations

                    class Migration(migrations.Migration):
                        dependencies = [("users", "0001_initial")]
                        operations = []
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            self._refresh(contract, "extras.0100_issue88_shard_02_extras_schema", baseline)
            self._assert_rejected(root, contract, "references deleted historical migration")

    def test_rejects_run_before_on_deleted_historical_migration(self):
        temporary_directory, root, contract, bootstrap, _baseline, _post = self._fixture()
        with temporary_directory:
            bootstrap.write_text(
                bootstrap.read_text(encoding="utf-8").replace(
                    "dependencies = []",
                    'dependencies = []\n    run_before = [("users", "0001_initial")]\n',
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "users.0000_issue88_shard_01_users_bootstrap", bootstrap)
            self._assert_rejected(root, contract, "references deleted historical migration")

    def test_rejects_unexpected_historical_file(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            self._write_migration(
                root,
                "users",
                "0001_initial",
                """
                from django.db import migrations

                class Migration(migrations.Migration):
                    dependencies = []
                    operations = []
                """,
            )
            self._assert_rejected(root, contract, "normalized baseline IDs")

    def test_rejects_reintroduced_replaces(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "    operations = []\n",
                    "    replaces = []\n    operations = []\n",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "extras.0100_issue88_shard_02_extras_schema", baseline)
            self._assert_rejected(root, contract, "must not contain a replaces declaration")

    def test_rejects_unknown_first_party_dependency(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "0000_issue88_shard_01_users_bootstrap",
                    "9999_unknown",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "extras.0100_issue88_shard_02_extras_schema", baseline)
            self._assert_rejected(root, contract, "unknown first-party normalized migration reference")

    def test_rejects_new_root_or_disconnected_baseline(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    'dependencies = [("users", "0000_issue88_shard_01_users_bootstrap")]',
                    "dependencies = []",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "extras.0100_issue88_shard_02_extras_schema", baseline)
            self._assert_rejected(root, contract, r"normalized baseline (?:roots|order/dependency) mismatch")

    def test_rejects_contract_root_mismatch(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["root_ids"] = [contract["baseline_leaf_ids"][0]]
            self._assert_rejected(root, contract, "normalized baseline roots mismatch")

    def test_rejects_a_cycle(self):
        temporary_directory, root, contract, bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            bootstrap.write_text(
                bootstrap.read_text(encoding="utf-8").replace(
                    "dependencies = []",
                    'dependencies = [("extras", "0100_issue88_shard_02_extras_schema")]',
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "users.0000_issue88_shard_01_users_bootstrap", bootstrap)
            self._assert_rejected(root, contract, "normalized baseline")

    def test_rejects_post_transition_leaf_gap(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["post_transition_leaf_ids"] = []
            self._assert_rejected(root, contract, "normalized post-transition leaves mismatch")

    def test_rejects_special_bootstrap_contract_mutation(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["special_users_bootstrap"] = contract["baseline_ids"][1]
            self._assert_rejected(root, contract, "must preserve the shipped users bootstrap")

    def test_rejects_contract_baseline_leaf_mismatch(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            contract["baseline_leaf_ids"] = [contract["baseline_ids"][0]]
            self._assert_rejected(root, contract, "normalized baseline leaves mismatch")

    def test_rejects_stale_semantic_policy_entry(self):
        temporary_directory, root, contract, *_ = self._fixture()
        with temporary_directory:
            self._assert_rejected(
                root,
                contract,
                "semantic policy coverage mismatch",
                semantic_dispositions={
                    "assets.0003_seed_status_labels": {
                        "disposition": "required-fresh",
                        "rationale": "stale policy entry",
                    }
                },
            )

    def test_rejects_unclassified_custom_operation(self):
        temporary_directory, root, contract, _bootstrap, baseline, _post = self._fixture()
        with temporary_directory:
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "operations = []",
                    "operations = [migrations.RunPython(lambda apps, schema_editor: None)]",
                ),
                encoding="utf-8",
            )
            self._refresh(contract, "extras.0100_issue88_shard_02_extras_schema", baseline)
            self._assert_rejected(root, contract, "semantic policy coverage mismatch")


if __name__ == "__main__":
    unittest.main()
