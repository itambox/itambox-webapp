import hashlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


@pytest.mark.serial_only
class AssetTypeFoundationMigrationTests(TransactionTestCase):
    migrate_from = [
        ("assets", "0101_seed_canonical_missing_status"),
        ("extras", "0113_upgrade_legacy_webhook_retry_schedules"),
    ]
    migrate_to = [("assets", "0104_asset_type_composition_backfill"), ("extras", "0114_asset_type_definition_schema")]

    def setUp(self):
        super().setUp()
        recorder = MigrationRecorder(connection)
        for migration_name in ("0104_asset_type_composition_backfill", "0103_asset_type_data_backfill"):
            if recorder.migration_qs.filter(app="assets", name=migration_name).exists():
                recorder.record_unapplied("assets", migration_name)

    def test_legacy_definitions_values_and_composition_are_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        ContentType = old_apps.get_model("contenttypes", "ContentType")
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")

        asset_type_ct = ContentType.objects.get(app_label="assets", model="assettype")
        memory = CustomField.objects.create(name="ram_gb", label="RAM (GB)", field_type="number")
        memory.object_types.add(asset_type_ct)
        environment = CustomField.objects.create(
            name="environment",
            label="Environment",
            field_type="select",
            choices="Production\nStaging",
        )
        environment.object_types.add(asset_type_ct)
        fieldset = CustomFieldset.objects.create(name="Issue 479 Migration Specs")
        fieldset.fields.add(memory)
        fieldset.fields.add(environment)
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Server",
            slug="example-server",
            custom_fieldset=fieldset,
            custom_field_data={"ram_gb": 16, "environment": "Production", "snipeit_id": 42},
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps

        CustomField = migrated_apps.get_model("extras", "CustomField")
        CustomFieldset = migrated_apps.get_model("extras", "CustomFieldset")
        AssetType = migrated_apps.get_model("assets", "AssetType")
        migrated_memory = CustomField.objects.get(name="memory_capacity")
        migrated_environment = CustomField.objects.get(name="environment")
        migrated_fieldset = CustomFieldset.objects.get(pk=fieldset.pk)
        migrated_asset_type = AssetType.objects.get(pk=asset_type.pk)

        self.assertEqual((migrated_memory.field_type, migrated_memory.decimal_scale), ("decimal", 3))
        self.assertEqual(migrated_environment.field_type, "single-select")
        self.assertEqual(
            list(migrated_environment.choice_set.choices.values_list("key", "label", "position")),
            [("production", "Production", 10), ("staging", "Staging", 20)],
        )
        self.assertEqual(
            (migrated_fieldset.namespace, migrated_fieldset.label),
            ("local", "Issue 479 Migration Specs"),
        )
        self.assertEqual(
            list(migrated_fieldset.field_memberships.values_list("custom_field__name", "position")),
            [("memory_capacity", 10), ("environment", 20)],
        )
        self.assertEqual(
            migrated_asset_type.custom_field_data,
            {"memory_capacity": "16.000", "environment": "production", "snipeit_id": 42},
        )
        self.assertEqual(
            list(migrated_asset_type.fieldset_memberships.values_list("fieldset_id", "position")),
            [(fieldset.pk, 10)],
        )

    def test_case_folded_fieldset_slugs_are_hashed_by_source_identity(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        lower = CustomFieldset.objects.create(name="foo")
        upper = CustomFieldset.objects.create(name="Foo")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        CustomFieldset = migrated_apps.get_model("extras", "CustomFieldset")

        def expected_slug(source):
            values = ("fieldset", source.name, source.pk)
            encoded = []
            for value in values:
                raw = str(value).encode("utf-8")
                encoded.append(str(len(raw)).encode("ascii") + b":" + raw)
            digest = hashlib.sha256(b"\x1f".join(encoded)).hexdigest()[:12]
            return f"foo-h{digest}"

        migrated_lower = CustomFieldset.objects.get(pk=lower.pk)
        migrated_upper = CustomFieldset.objects.get(pk=upper.pk)
        self.assertEqual(migrated_lower.slug, expected_slug(lower))
        self.assertEqual(migrated_upper.slug, expected_slug(upper))
        self.assertNotEqual(migrated_lower.slug, migrated_upper.slug)
