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

    def test_repository_graph_and_reviewed_semantics_are_exact(self):
        repository_root = Path(__file__).resolve().parents[2]

        inventory = build_inventory(repository_root / "itambox")

        self.assertEqual(inventory["summary"]["first_party_nodes"], 262)
        self.assertEqual(inventory["summary"]["first_party_edges"], 522)
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
            ],
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


if __name__ == "__main__":
    unittest.main()
