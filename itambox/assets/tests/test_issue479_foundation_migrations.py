import hashlib
import importlib
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone


class AdoptionPreflightUnitTests(SimpleTestCase):
    migration = importlib.import_module("assets.migrations.0103_asset_type_data_backfill")

    @staticmethod
    def _field(name, label, field_type, content_type):
        content_type_manager = SimpleNamespace(all=lambda: [content_type])
        return SimpleNamespace(
            name=name,
            label=label,
            field_type=field_type,
            choices="",
            required=False,
            deleted_at=None,
            object_types=content_type_manager,
        )

    def test_partial_adoption_set_is_rejected_before_any_source_mutation(self):
        migration = self.migration
        content_type = SimpleNamespace(app_label="assets", model="assettype")
        field = self._field("ram_gb", "RAM (GB)", "number", content_type)
        before = (field.name, field.label, field.field_type)
        with pytest.raises(RuntimeError, match="adoption_source_set"):
            migration._preflight_adoption_sources(
                [field],
                {"ram_gb": migration._expected_adoption_signature("ram_gb")},
                {"ram_gb": "memory_capacity"},
            )
        self.assertEqual((field.name, field.label, field.field_type), before)

    def test_changed_adoption_preimage_is_rejected_without_mutation(self):
        migration = self.migration
        content_types = {
            "asset_type": SimpleNamespace(app_label="assets", model="assettype"),
            "asset": SimpleNamespace(app_label="assets", model="asset"),
        }
        fields = []
        signatures = {}
        key_map = {}
        for source_key, (
            label,
            field_type,
            _,
            _,
            expected_types,
        ) in migration.EXPECTED_ADOPTION_SOURCE_PREIMAGES.items():
            content_type = content_types["asset_type" if "assettype" in next(iter(expected_types)) else "asset"]
            actual_label = "Host name changed" if source_key == "hostname" else label
            field = self._field(source_key, actual_label, field_type, content_type)
            fields.append(field)
            signatures[source_key] = migration._signature(
                field,
                [f"{content_type.app_label}.{content_type.model}"],
            )
            key_map[source_key] = migration.ADOPTION_FIELDS[source_key]["name"]
        before = [(field.name, field.label, field.field_type) for field in fields]
        with pytest.raises(RuntimeError, match="adoption_source_signature"):
            migration._preflight_adoption_sources(fields, signatures, key_map)
        self.assertEqual([(field.name, field.label, field.field_type) for field in fields], before)


@pytest.mark.serial_only
class AssetTypeFoundationMigrationTests(TransactionTestCase):
    migrate_from = [
        ("assets", "0101_seed_canonical_missing_status"),
        ("extras", "0113_upgrade_legacy_webhook_retry_schedules"),
    ]
    migrate_to = [("assets", "0104_asset_type_composition_backfill"), ("extras", "0114_asset_type_definition_schema")]

    def setUp(self):
        super().setUp()
        self._schema_name = f"issue479_{os.getpid()}_{uuid4().hex[:12]}"
        quoted_schema = connection.ops.quote_name(self._schema_name)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {quoted_schema}")
            cursor.execute(f"SET search_path TO {quoted_schema}")
            MigrationRecorder(connection).ensure_schema()
            cursor.execute(f"SET search_path TO {quoted_schema}, public")

    def tearDown(self):
        quoted_schema = connection.ops.quote_name(self._schema_name)
        try:
            super().tearDown()
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET search_path TO public")
                    cursor.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            finally:
                ContentType.objects.clear_cache()

    def test_core_seed_creates_object_type_contenttypes_on_fresh_upgrade(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        ContentType._base_manager.filter(app_label="assets", model__in=["asset", "assettype"]).delete()

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        MigrationExecutor(connection).migrate(target)
        migrated_apps = MigrationExecutor(connection).loader.project_state(target).apps
        CustomField = migrated_apps.get_model("extras", "CustomField")
        for name, expected_models in (
            ("processor_model", {"asset", "assettype"}),
            ("hostname", {"asset"}),
        ):
            field = CustomField.objects.get(name=name)
            self.assertEqual(set(field.object_types.values_list("model", flat=True)), expected_models)

    def test_core_seed_rejects_values_invalidated_by_core_contract(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0102_asset_type_composition_schema"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        asset_type_ct = ContentType.objects.get_or_create(app_label="assets", model="assettype")[0]
        asset_ct = ContentType.objects.get_or_create(app_label="assets", model="asset")[0]
        CustomField = old_apps.get_model("extras", "CustomField")
        source_fields = (
            ("ram_gb", "RAM (GB)", "number", asset_type_ct),
            ("storage_gb", "Storage (GB)", "number", asset_type_ct),
            ("poe_budget_w", "PoE Budget (Watts)", "number", asset_type_ct),
            ("hostname", "Hostname", "text", asset_ct),
            ("firmware_version", "Firmware Version", "text", asset_ct),
        )
        for name, label, field_type, content_type in source_fields:
            field = CustomField.objects.create(name=name, label=label, field_type=field_type, choices="")
            field.object_types.add(content_type)
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")
        manufacturer = Manufacturer.objects.create(name="Invalid Core Value Maker", slug="invalid-core-value-maker")
        AssetType.objects.create(
            manufacturer=manufacturer,
            model="Invalid Core Value Model",
            slug="invalid-core-value-model",
            custom_field_data={"analog_input_count": "1.5", "operating_system_family": "not-a-choice"},
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        with self.assertRaisesRegex(RuntimeError, "issue479:core_value"):
            MigrationExecutor(connection).migrate(target)

    def test_core_seed_rejects_malformed_decimal_text(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0102_asset_type_composition_schema"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")
        manufacturer = Manufacturer.objects.create(name="Malformed Decimal Maker", slug="malformed-decimal-maker")
        AssetType.objects.create(
            manufacturer=manufacturer,
            model="Malformed Decimal Model",
            slug="malformed-decimal-model",
            custom_field_data={"memory_capacity": "1x000"},
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        with self.assertRaisesRegex(RuntimeError, "issue479:core_value"):
            MigrationExecutor(connection).migrate(target)

    def test_core_seed_rejects_signed_zero_decimal_text(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0102_asset_type_composition_schema"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")
        manufacturer = Manufacturer.objects.create(name="Signed Zero Maker", slug="signed-zero-maker")
        AssetType.objects.create(
            manufacturer=manufacturer,
            model="Signed Zero Model",
            slug="signed-zero-model",
            custom_field_data={"memory_capacity": "-0.000"},
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        with self.assertRaisesRegex(RuntimeError, "issue479:core_value"):
            MigrationExecutor(connection).migrate(target)

    def test_core_seed_rejects_inverted_temperature_range(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0102_asset_type_composition_schema"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")
        manufacturer = Manufacturer.objects.create(name="Inverted Temperature Maker", slug="inverted-temperature-maker")
        AssetType.objects.create(
            manufacturer=manufacturer,
            model="Inverted Temperature Model",
            slug="inverted-temperature-model",
            custom_field_data={
                "operating_temperature_min": "10.00",
                "operating_temperature_max": "-10.00",
            },
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        with self.assertRaisesRegex(RuntimeError, "issue479:core_value"):
            MigrationExecutor(connection).migrate(target)

    def test_fieldset_cutover_catches_up_post_0103_legacy_rows(self):
        executor = MigrationExecutor(connection)
        pre_cutover = [
            ("assets", "0104_asset_type_composition_backfill"),
            ("extras", "0114_asset_type_definition_schema"),
        ]
        executor.migrate(pre_cutover)
        old_apps = executor.loader.project_state(pre_cutover).apps
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")

        field = CustomField.objects.create(
            name="legacy_select_late",
            label="Late Legacy Select",
            field_type="select",
            choices="Production\nStaging",
        )
        stable_field = CustomField.objects.create(
            name="legacy_select_stable",
            label="Stable Legacy Select",
            field_type="select",
            choices="stable",
        )
        fieldset = CustomFieldset.objects.create(
            name="Late Legacy Fieldset",
            slug=None,
        )
        manufacturer = Manufacturer.objects.create(name="Late Legacy Maker")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Late Legacy Model",
            slug="late-legacy-model",
            custom_field_data={"legacy_select_late": "Production", "legacy_select_stable": "stable"},
        )
        fieldset.legacy_fields.add(field, stable_field)

        target = [("assets", "0107_asset_type_library_contract"), ("extras", "0115_asset_type_fieldset_cutover")]
        MigrationExecutor(connection).migrate(target)
        migrated_apps = MigrationExecutor(connection).loader.project_state(target).apps
        CustomField = migrated_apps.get_model("extras", "CustomField")
        CustomFieldset = migrated_apps.get_model("extras", "CustomFieldset")
        AssetType = migrated_apps.get_model("assets", "AssetType")

        migrated_field = CustomField.objects.get(pk=field.pk)
        migrated_stable_field = CustomField.objects.get(pk=stable_field.pk)
        migrated_fieldset = CustomFieldset.objects.get(pk=fieldset.pk)
        migrated_asset_type = AssetType.objects.get(pk=asset_type.pk)
        self.assertEqual(migrated_field.field_type, "single-select")
        self.assertEqual(migrated_stable_field.field_type, "single-select")
        self.assertEqual(
            list(migrated_stable_field.choice_set.choices.values_list("label", flat=True)),
            ["stable"],
        )
        self.assertEqual(
            list(migrated_field.choice_set.choices.values_list("label", flat=True)),
            ["Production", "Staging"],
        )
        self.assertTrue(migrated_fieldset.slug)
        self.assertEqual(migrated_asset_type.custom_field_data["legacy_select_late"], "production")
        self.assertEqual(migrated_asset_type.custom_field_data["legacy_select_stable"], "stable")
        self.assertEqual(
            list(migrated_fieldset.fields.values_list("name", flat=True)),
            ["legacy_select_late", "legacy_select_stable"],
        )

    def test_fieldset_cutover_backfills_unique_non_null_slugs(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        first = CustomFieldset.objects.create(name="Legacy Fieldset")
        second = CustomFieldset.objects.create(name="Legacy-Fieldset")

        target = [("assets", "0107_asset_type_library_contract"), ("extras", "0115_asset_type_fieldset_cutover")]
        MigrationExecutor(connection).migrate(target)
        migrated_apps = MigrationExecutor(connection).loader.project_state(target).apps
        CustomFieldset = migrated_apps.get_model("extras", "CustomFieldset")
        migrated_first = CustomFieldset.objects.get(pk=first.pk)
        migrated_second = CustomFieldset.objects.get(pk=second.pk)
        self.assertFalse(migrated_apps.get_model("extras", "CustomFieldset")._meta.get_field("slug").null)
        self.assertTrue(migrated_first.slug)
        self.assertTrue(migrated_second.slug)
        self.assertNotEqual(migrated_first.slug, migrated_second.slug)

    def test_legacy_definitions_values_and_composition_are_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        ContentType = old_apps.get_model("contenttypes", "ContentType")
        for model in ("assettype", "asset"):
            ContentType.objects.get_or_create(
                app_label="assets",
                model=model,
            )
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        AssetType = old_apps.get_model("assets", "AssetType")

        asset_type_ct = ContentType.objects.get(app_label="assets", model="assettype")
        memory = CustomField.objects.create(name="ram_gb", label="RAM (GB)", field_type="number", choices="")
        memory.object_types.add(asset_type_ct)
        storage = CustomField.objects.create(name="storage_gb", label="Storage (GB)", field_type="number", choices="")
        storage.object_types.add(asset_type_ct)
        poe = CustomField.objects.create(
            name="poe_budget_w", label="PoE Budget (Watts)", field_type="number", choices=""
        )
        poe.object_types.add(asset_type_ct)
        hostname = CustomField.objects.create(name="hostname", label="Hostname", field_type="text", choices="")
        hostname.object_types.add(ContentType.objects.get(app_label="assets", model="asset"))
        firmware = CustomField.objects.create(
            name="firmware_version", label="Firmware Version", field_type="text", choices=""
        )
        firmware.object_types.add(ContentType.objects.get(app_label="assets", model="asset"))
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

        executor = MigrationExecutor(connection)
        executor.migrate([("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")])
        adopted_apps = executor.loader.project_state(
            [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        ).apps
        adopted_memory = adopted_apps.get_model("extras", "CustomField").objects.get(name="memory_capacity")
        self.assertEqual((adopted_memory.field_type, adopted_memory.management_kind), ("decimal", "core"))

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

    def test_core_seed_refuses_local_field_identity(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomField.objects.create(
            name="form_factor",
            label="Local form factor",
            field_type="text",
            namespace="local",
            management_kind="local",
            version=1,
            lifecycle="active",
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_field_identity_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_refuses_fieldset_identity(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        CustomFieldset.objects.create(
            name="Local compute memory",
            namespace="itambox",
            slug="compute-memory",
            label="Local compute memory",
            management_kind="local",
            lifecycle="active",
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_fieldset_identity_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_refuses_unexpected_fieldset_membership(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        CustomFieldsetField = old_apps.get_model("extras", "CustomFieldsetField")
        fieldset = CustomFieldset.objects.create(
            name="Compute memory",
            namespace="itambox",
            slug="compute-memory",
            label="Compute memory",
            management_kind="core",
            lifecycle="active",
        )
        unexpected = CustomField.objects.create(
            name="unexpected_field",
            label="Unexpected field",
            field_type="text",
            scope="asset_type",
            namespace="itambox",
            management_kind="core",
            lifecycle="active",
            version=1,
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=unexpected, position=999)

        with self.assertRaisesRegex(RuntimeError, "issue479:core_fieldset_membership_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_allows_local_fieldset_same_slug(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        CustomFieldset.objects.create(
            name="Local compute memory",
            namespace="local",
            slug="compute-memory",
            label="Local compute memory",
            management_kind="local",
            lifecycle="active",
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        MigrationExecutor(connection).migrate(target)
        migrated_apps = MigrationExecutor(connection).loader.project_state(target).apps
        CustomFieldset = migrated_apps.get_model("extras", "CustomFieldset")
        self.assertTrue(CustomFieldset.objects.filter(namespace="local", slug="compute-memory").exists())
        self.assertTrue(CustomFieldset.objects.filter(namespace="itambox", slug="compute-memory").exists())

    def test_core_seed_refuses_fieldset_tombstone(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        CustomFieldset.objects.create(
            name="Deleted compute memory",
            namespace="itambox",
            slug="compute-memory",
            label="Deleted compute memory",
            management_kind="local",
            lifecycle="deleted",
            deleted_at=timezone.now(),
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_fieldset_identity_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_allows_local_choice_set_same_slug(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ChoiceSet = old_apps.get_model("extras", "CustomFieldChoiceSet")
        ChoiceSet.objects.create(
            namespace="local",
            slug="form-factor",
            label="Local form factor",
            management_kind="local",
            lifecycle="active",
        )

        target = [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
        MigrationExecutor(connection).migrate(target)
        migrated_apps = MigrationExecutor(connection).loader.project_state(target).apps
        ChoiceSet = migrated_apps.get_model("extras", "CustomFieldChoiceSet")
        self.assertTrue(ChoiceSet.objects.filter(namespace="local", slug="form-factor").exists())
        self.assertTrue(ChoiceSet.objects.filter(namespace="itambox", slug="form-factor").exists())

    def test_core_seed_refuses_field_lifecycle_collision(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ChoiceSet = old_apps.get_model("extras", "CustomFieldChoiceSet")
        CustomField = old_apps.get_model("extras", "CustomField")
        choice_set = ChoiceSet.objects.create(
            namespace="itambox",
            slug="form-factor",
            label="Form factor",
            management_kind="core",
            lifecycle="active",
        )
        CustomField.objects.create(
            name="form_factor",
            label="Form factor",
            field_type="single-select",
            scope="asset_type",
            namespace="itambox",
            choice_set=choice_set,
            management_kind="core",
            lifecycle="deprecated",
            version=1,
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_field_identity_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_refuses_choice_lifecycle_collision(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ChoiceSet = old_apps.get_model("extras", "CustomFieldChoiceSet")
        Choice = old_apps.get_model("extras", "CustomFieldChoice")
        choice_set = ChoiceSet.objects.create(
            namespace="itambox",
            slug="form-factor",
            label="Form factor",
            management_kind="core",
            lifecycle="active",
        )
        Choice.objects.create(
            choice_set=choice_set,
            key="notebook",
            label="Notebook",
            position=999,
            management_kind="core",
            lifecycle="deprecated",
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_choice_set_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_core_seed_refuses_choice_set_tombstone(self):
        executor = MigrationExecutor(connection)
        pre_seed = [("assets", "0105_asset_type_core_adoption"), ("extras", "0114_asset_type_definition_schema")]
        executor.migrate(pre_seed)
        old_apps = executor.loader.project_state(pre_seed).apps
        ChoiceSet = old_apps.get_model("extras", "CustomFieldChoiceSet")
        ChoiceSet.objects.create(
            namespace="itambox",
            slug="form-factor",
            label="Reserved",
            management_kind="local",
            lifecycle="deleted",
            deleted_at=timezone.now(),
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:core_choice_set_collision"):
            MigrationExecutor(connection).migrate(
                [("assets", "0106_asset_type_core_seed"), ("extras", "0114_asset_type_definition_schema")]
            )

    def test_asset_type_cutover_reverse_is_explicitly_refused(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [("assets", "0108_asset_type_singular_cutover"), ("extras", "0115_asset_type_fieldset_cutover")]
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:reverse_refused"):
            MigrationExecutor(connection).migrate(
                [("assets", "0107_asset_type_library_contract"), ("extras", "0115_asset_type_fieldset_cutover")]
            )

    def test_fieldset_cutover_reverse_is_explicitly_refused(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [("assets", "0107_asset_type_library_contract"), ("extras", "0115_asset_type_fieldset_cutover")]
        )

        with self.assertRaisesRegex(RuntimeError, "issue479:reverse_refused"):
            MigrationExecutor(connection).migrate(
                [("assets", "0107_asset_type_library_contract"), ("extras", "0114_asset_type_definition_schema")]
            )
