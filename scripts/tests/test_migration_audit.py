import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.migration_audit import (
    SEMANTIC_DISPOSITIONS,
    build_inventory,
    render_inventory,
)


class MigrationAuditTests(unittest.TestCase):
    def test_inventory_classifies_graph_and_special_operations(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_migration(
                root,
                "users",
                "0001_initial",
                """
                from django.conf import settings
                from django.db import migrations

                def seed(apps, schema_editor):
                    pass

                def undo(apps, schema_editor):
                    pass

                class Migration(migrations.Migration):
                    initial = True
                    dependencies = [
                        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    ]
                    operations = [
                        migrations.RunPython(seed, undo),
                        migrations.RunSQL("SELECT 1", reverse_sql=migrations.RunSQL.noop),
                    ]
                """,
            )
            self._write_migration(
                root,
                "beta",
                "0001_initial",
                """
                from django.db import migrations

                class Migration(migrations.Migration):
                    dependencies = [("users", "0001_initial")]
                    operations = [
                        migrations.SeparateDatabaseAndState(
                            database_operations=[],
                            state_operations=[],
                        ),
                        migrations.BtreeGistExtension(),
                    ]
                """,
            )
            self._write_migration(
                root,
                "users",
                "0010_user",
                """
                from django.db import migrations

                class Migration(migrations.Migration):
                    dependencies = []
                    run_before = [("users", "0001_initial")]
                    operations = []
                """,
            )

            inventory = build_inventory(
                root,
                semantic_dispositions={
                    "beta.0001_initial": {
                        "disposition": "safely-replaced-by-final-schema",
                        "rationale": "Synthetic state/database fixture.",
                    },
                    "users.0001_initial": {
                        "disposition": "required-fresh",
                        "rationale": "Synthetic seed fixture.",
                    },
                },
                expected_blockers=[],
            )

        self.assertEqual(inventory["summary"]["first_party_nodes"], 3)
        self.assertEqual(inventory["summary"]["first_party_edges"], 2)
        self.assertEqual(inventory["summary"]["global_roots"], ["users.0010_user"])
        self.assertEqual(inventory["summary"]["global_leaves"], ["beta.0001_initial"])
        self.assertEqual(
            inventory["special_users_bootstrap"]["migration"],
            "users.0010_user",
        )
        self.assertEqual(
            inventory["special_users_bootstrap"]["run_before"],
            ["users.0001_initial"],
        )
        self.assertEqual(
            len(inventory["special_users_bootstrap"]["swappable_dependents"]),
            1,
        )
        users = next(
            migration
            for migration in inventory["migrations"]
            if migration["id"] == "users.0001_initial"
        )
        self.assertEqual(users["operations"]["RunPython"]["with_reverse"], 1)
        self.assertEqual(users["operations"]["RunSQL"]["with_noop_reverse"], 1)
        beta = next(
            migration
            for migration in inventory["migrations"]
            if migration["id"] == "beta.0001_initial"
        )
        self.assertEqual(beta["operations"]["BtreeGistExtension"]["count"], 1)
        self.assertEqual(
            inventory["reviewed_semantics"]["required_fresh"],
            ["users.0001_initial"],
        )

    def test_render_is_deterministic_and_does_not_import_migrations(self):
        source = """
        raise RuntimeError("this migration must never be imported")
        from django.db import migrations
        class Migration(migrations.Migration):
            dependencies = []
            operations = []
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = self._write_migration(root, "alpha", "0001_initial", source)
            before = path.read_text(encoding="utf-8")

            first = render_inventory(
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])
            )
            second = render_inventory(
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])
            )
            after = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        json.loads(first)

    def test_replacements_partition_historical_migrations_and_summarize_quotient(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_migration(
                root,
                "alpha",
                "0002_second",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = [("alpha", "0001_initial")]
                    operations = []
                """,
            )
            self._write_migration(
                root,
                "beta",
                "0001_initial",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = [("alpha", "0002_second")]
                    operations = []
                """,
            )
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial"), ("alpha", "0002_second")],
            )
            self._write_replacement(
                root,
                "beta",
                "0001_squashed",
                [("beta", "0001_initial")],
            )

            inventory = build_inventory(
                root, semantic_dispositions={}, expected_blockers=[]
            )

        self.assertEqual(
            inventory["historical_graph"],
            {
                "edges": [
                    ["alpha.0001_initial", "alpha.0002_second"],
                    ["alpha.0002_second", "beta.0001_initial"],
                ],
                "leaves": ["beta.0001_initial"],
                "nodes": [
                    "alpha.0001_initial",
                    "alpha.0002_second",
                    "beta.0001_initial",
                ],
                "roots": ["alpha.0001_initial"],
            },
        )
        self.assertEqual(
            inventory["effective_graph"],
            {
                "edges": [["alpha.0001_squashed", "beta.0001_squashed"]],
                "leaves": ["beta.0001_squashed"],
                "nodes": ["alpha.0001_squashed", "beta.0001_squashed"],
                "roots": ["alpha.0001_squashed"],
            },
        )
        self.assertEqual(
            [
                (migration["id"], migration["is_replacement"], migration["replaces"])
                for migration in inventory["migrations"]
            ],
            [
                ("alpha.0001_initial", False, []),
                (
                    "alpha.0001_squashed",
                    True,
                    ["alpha.0001_initial", "alpha.0002_second"],
                ),
                ("alpha.0002_second", False, []),
                ("beta.0001_initial", False, []),
                ("beta.0001_squashed", True, ["beta.0001_initial"]),
            ],
        )

    def test_replacement_targets_must_exist(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial"), ("alpha", "0099_missing")],
            )

            with self.assertRaisesRegex(ValueError, "unknown replacement targets"):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_replacements_must_cover_every_historical_migration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_plain_migration(root, "alpha", "0002_second")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
            )

            with self.assertRaisesRegex(
                ValueError, r"replacement coverage incomplete.*alpha\.0002_second"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_empty_replaces_declaration_triggers_coverage_validation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(root, "alpha", "0001_squashed", [])

            with self.assertRaisesRegex(
                ValueError, r"replacement coverage incomplete.*alpha\.0001_initial"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_replacement_targets_must_not_be_duplicated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial"), ("alpha", "0001_initial")],
            )

            with self.assertRaisesRegex(
                ValueError, r"duplicate replacement targets.*alpha\.0001_initial"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_effective_replacement_quotient_must_be_acyclic(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_migration(
                root,
                "beta",
                "0001_initial",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = [("alpha", "0001_initial")]
                    operations = []
                """,
            )
            self._write_migration(
                root,
                "beta",
                "0002_second",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = []
                    operations = []
                """,
            )
            self._write_migration(
                root,
                "alpha",
                "0002_second",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = [("beta", "0002_second")]
                    operations = []
                """,
            )
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial"), ("alpha", "0002_second")],
            )
            self._write_replacement(
                root,
                "beta",
                "0001_squashed",
                [("beta", "0001_initial"), ("beta", "0002_second")],
            )

            with self.assertRaisesRegex(
                ValueError, "effective replacement graph contains a cycle"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_effective_graph_includes_explicit_replacement_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_plain_migration(root, "beta", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
            )
            self._write_replacement(
                root,
                "beta",
                "0001_squashed",
                [("beta", "0001_initial")],
                dependencies=[("alpha", "0001_squashed")],
            )

            inventory = build_inventory(
                root, semantic_dispositions={}, expected_blockers=[]
            )

        self.assertEqual(
            inventory["effective_graph"]["edges"],
            [["alpha.0001_squashed", "beta.0001_squashed"]],
        )

    def test_effective_graph_includes_explicit_replacement_run_before_edges(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_plain_migration(root, "beta", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
                run_before=[("beta", "0001_squashed")],
            )
            self._write_replacement(
                root,
                "beta",
                "0001_squashed",
                [("beta", "0001_initial")],
            )

            inventory = build_inventory(
                root, semantic_dispositions={}, expected_blockers=[]
            )

        self.assertEqual(
            inventory["effective_graph"]["edges"],
            [["alpha.0001_squashed", "beta.0001_squashed"]],
        )

    def test_effective_graph_rejects_cycle_in_explicit_and_redirected_edge_union(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_migration(
                root,
                "beta",
                "0001_initial",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = [("alpha", "0001_initial")]
                    operations = []
                """,
            )
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
                dependencies=[("beta", "0001_squashed")],
            )
            self._write_replacement(
                root,
                "beta",
                "0001_squashed",
                [("beta", "0001_initial")],
            )

            with self.assertRaisesRegex(
                ValueError, "effective replacement graph contains a cycle"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_unknown_first_party_explicit_replacement_dependency_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
                dependencies=[("alpha", "0099_missing")],
            )

            with self.assertRaisesRegex(
                ValueError,
                r"unknown first-party replacement dependency targets.*alpha\.0099_missing",
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_third_party_replacement_dependencies_are_outside_effective_graph(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0001_squashed",
                [("alpha", "0001_initial")],
                dependencies=[("contenttypes", "0002_remove_content_type_name")],
            )

            inventory = build_inventory(
                root, semantic_dispositions={}, expected_blockers=[]
            )

        self.assertEqual(inventory["effective_graph"]["edges"], [])
        self.assertEqual(
            inventory["effective_graph"]["nodes"], ["alpha.0001_squashed"]
        )

    def test_issue88_shard_requires_immediate_predecessor_dependency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_plain_migration(root, "beta", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0000_issue88_shard_01_alpha",
                [("alpha", "0001_initial")],
            )
            self._write_replacement(
                root,
                "beta",
                "0100_issue88_shard_02_beta",
                [("beta", "0001_initial")],
            )

            with self.assertRaisesRegex(
                ValueError, "issue88 shard lacks immediate predecessor"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_issue88_shard_requires_previous_same_app_dependency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_plain_migration(root, "alpha", "0002_second")
            self._write_plain_migration(root, "beta", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0000_issue88_shard_01_alpha",
                [("alpha", "0001_initial")],
            )
            self._write_replacement(
                root,
                "beta",
                "0100_issue88_shard_02_beta",
                [("beta", "0001_initial")],
                dependencies=[("alpha", "0000_issue88_shard_01_alpha")],
            )
            self._write_replacement(
                root,
                "alpha",
                "0100_issue88_shard_03_alpha",
                [("alpha", "0002_second")],
                dependencies=[("beta", "0100_issue88_shard_02_beta")],
            )

            with self.assertRaisesRegex(
                ValueError, "issue88 shard lacks previous same-app shard"
            ):
                build_inventory(root, semantic_dispositions={}, expected_blockers=[])

    def test_post_transition_migration_requires_effective_leaf_dependency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_plain_migration(root, "alpha", "0001_initial")
            self._write_replacement(
                root,
                "alpha",
                "0000_issue88_shard_01_alpha",
                [("alpha", "0001_initial")],
            )
            self._write_migration(
                root,
                "extras",
                "0101_issue88_drop_legacy_webhook_name_like",
                """
                from django.db import migrations
                class Migration(migrations.Migration):
                    dependencies = []
                    operations = [
                        migrations.RunSQL(
                            "DROP INDEX IF EXISTS legacy_index",
                            reverse_sql=migrations.RunSQL.noop,
                        ),
                    ]
                """,
            )
            dispositions = {
                "extras.0101_issue88_drop_legacy_webhook_name_like": {
                    "disposition": "upgrade-only",
                    "rationale": "Synthetic post-transition fixture.",
                },
            }

            with self.assertRaisesRegex(
                ValueError, "post-transition migration lacks effective leaf dependency"
            ):
                build_inventory(
                    root,
                    semantic_dispositions=dispositions,
                    expected_blockers=[],
                )

    def test_repository_graph_and_reviewed_semantics_are_exact(self):
        repository_root = Path(__file__).resolve().parents[2]

        inventory = build_inventory(repository_root / "itambox")

        self.assertEqual(inventory["summary"]["first_party_nodes"], 262)
        self.assertEqual(inventory["summary"]["first_party_edges"], 522)
        self.assertEqual(inventory["summary"]["replacement_shards"], 62)
        self.assertEqual(inventory["summary"]["replacement_targets"], 262)
        self.assertEqual(inventory["summary"]["explicit_replacement_chain_edges"], 61)
        self.assertEqual(inventory["summary"]["post_transition_migrations"], 1)
        self.assertEqual(
            inventory["post_transition_migrations"],
            ["extras.0101_issue88_drop_legacy_webhook_name_like"],
        )
        self.assertEqual(inventory["summary"]["missing_replacement_targets"], 0)
        self.assertEqual(inventory["summary"]["duplicate_replacement_targets"], 0)
        self.assertTrue(
            inventory["summary"]["effective_replacement_quotient_acyclic"]
        )
        self.assertEqual(
            inventory["summary"]["global_roots"],
            ["users.0010_user"],
        )
        self.assertEqual(
            inventory["summary"]["global_leaves"],
            [
                "assets.0056_remove_assetassignment_unique_active_assignment_per_asset_and_more",
                "compliance.0016_alter_assetaudit_asset_alter_assetaudit_auditor_and_more",
                "core.0032_alter_emailsettings_enabled_and_more",
                "extras.0041_alter_reporttemplate_style_preset",
                "inventory.0014_alter_accessorystock_tenant_and_more",
                "licenses.0013_licenseseatassignment_unique_active_license_seat_per_asset_and_more",
                "procurement.0012_alter_contract_contract_number_and_more",
                "users.0013_remove_usergroup_users_usergroup_unique_tenant_name_active_and_more",
            ],
        )
        self.assertEqual(
            inventory["summary"]["custom_operation_file_counts"],
            {
                "RunPython": 38,
                "RunSQL": 11,
                "SeparateDatabaseAndState": 24,
                "BtreeGistExtension": 1,
            },
        )
        self.assertEqual(
            inventory["special_users_bootstrap"]["migration"],
            "users.0010_user",
        )
        self.assertEqual(
            inventory["special_users_bootstrap"]["run_before"],
            ["users.0001_initial"],
        )
        self.assertEqual(
            len(inventory["special_users_bootstrap"]["swappable_dependents"]),
            52,
        )
        self.assertEqual(
            inventory["reviewed_semantics"]["required_fresh"],
            [
                "assets.0003_seed_status_labels",
                "assets.0043_seed_depreciation_policies",
                "assets.0051_assetreservation_assetreservation_no_overlap",
                "assets.0100_issue88_shard_42_assets_relations",
                "assets.0100_issue88_shard_43_assets_seed",
            ],
        )
        replacement_extension = next(
            migration
            for migration in inventory["migrations"]
            if migration["id"] == "assets.0100_issue88_shard_42_assets_relations"
        )
        self.assertEqual(
            replacement_extension["operations"]["BtreeGistExtension"]["count"],
            1,
        )
        self.assertEqual(
            inventory["reviewed_semantics"]["blockers"],
            [
                "organization.0026_remove_tenantrole_tenant_provider_tenant_provider_and_more",
                "organization.0027_drop_legacy_role_models",
                "organization.0034_roleassignment_remove_provider_internal_tenant_and_more",
                "organization.0035_delete_provider_and_more",
                "users.0008_alter_usergroup_options_token_provider_and_more",
                "users.0010_remove_usergroup_users_usergroup_unique_provider_name_active_and_more",
            ],
        )
        self.assertEqual(
            SEMANTIC_DISPOSITIONS["procurement.0004_setup_groups"]["disposition"],
            "upgrade-only",
        )
        self.assertEqual(
            inventory["reviewed_semantics"]["review_blocker"],
            ["organization.0027_drop_legacy_role_models"],
        )
        self.assertEqual(len(inventory["effective_graph"]["nodes"]), 62)
        self.assertEqual(
            inventory["effective_graph"]["roots"],
            ["users.0000_issue88_shard_01_users_bootstrap"],
        )
        self.assertEqual(
            inventory["effective_graph"]["leaves"],
            ["users.0100_issue88_shard_62_users_relations"],
        )

        custom_operation_ids = {
            migration["id"]
            for migration in inventory["migrations"]
            if any(migration["syntactic_facts"]["has_custom_operations"].values())
        }
        self.assertEqual(set(SEMANTIC_DISPOSITIONS), custom_operation_ids)
        self.assertFalse(set(SEMANTIC_DISPOSITIONS) - custom_operation_ids)
        self.assertEqual(
            {
                migration["id"]
                for migration in inventory["migrations"]
                if migration["reviewed_disposition"] is not None
            },
            custom_operation_ids,
        )

    @staticmethod
    def _write_migration(root, app, name, source):
        migration_directory = root / app / "migrations"
        migration_directory.mkdir(parents=True, exist_ok=True)
        path = migration_directory / f"{name}.py"
        normalized = textwrap.dedent(source).strip()
        path.write_text(normalized + "\n", encoding="utf-8")
        return path

    def _write_plain_migration(self, root, app, name):
        return self._write_migration(
            root,
            app,
            name,
            """
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = []
            """,
        )

    def _write_replacement(
        self, root, app, name, replaces, dependencies=None, run_before=None
    ):
        dependencies = [] if dependencies is None else dependencies
        run_before = [] if run_before is None else run_before
        return self._write_migration(
            root,
            app,
            name,
            f"""
            from django.db import migrations
            class Migration(migrations.Migration):
                replaces = {replaces!r}
                dependencies = {dependencies!r}
                run_before = {run_before!r}
                operations = []
            """,
        )


if __name__ == "__main__":
    unittest.main()
