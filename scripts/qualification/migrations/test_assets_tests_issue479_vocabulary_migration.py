"""Migration rehearsal for the normalized #479 core vocabulary.

Proves that the normalized migration graph lands exactly on the live runtime
vocabulary target, that the transitional residues of the old graph are gone,
and that legacy values survive the conversion. Run explicitly:

    PYTHONPATH=itambox pytest scripts/qualification/migrations/
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from assets.services.specifications.core_vocabulary import get_core_vocabulary
from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests

MIGRATE_FROM = [
    ("assets", "0101_seed_canonical_missing_status"),
    ("extras", "0113_upgrade_legacy_webhook_retry_schedules"),
]

MIGRATE_TO = [
    ("assets", "0107_asset_type_specification_guards"),
    ("extras", "0117_asset_type_definition_guards"),
]

ADOPTION_SOURCES = (
    ("ram_gb", "RAM (GB)", "number", ("assettype",)),
    ("storage_gb", "Storage (GB)", "number", ("assettype",)),
    ("poe_budget_w", "PoE Budget (Watts)", "number", ("assettype",)),
    ("hostname", "Hostname", "text", ("asset",)),
    ("firmware_version", "Firmware Version", "text", ("asset",)),
)


@isolate_migration_tests
@pytest.mark.serial_only
class NormalizedVocabularyMigrationTests(IsolatedMigrationTestCase):
    migrate_from = MIGRATE_FROM
    migrate_to = MIGRATE_TO

    def test_fresh_install_lands_on_runtime_target_without_residue(self):
        target_executor = MigrationExecutor(connection)
        target_executor.migrate(self.migrate_to)
        new_apps = target_executor.loader.project_state(self.migrate_to).apps
        CustomField = new_apps.get_model("extras", "CustomField")
        CustomFieldChoice = new_apps.get_model("extras", "CustomFieldChoice")
        CustomFieldChoiceSet = new_apps.get_model("extras", "CustomFieldChoiceSet")
        CustomFieldset = new_apps.get_model("extras", "CustomFieldset")
        Category = new_apps.get_model("assets", "Category")

        target = get_core_vocabulary()

        core_fields = {
            field.name: field
            for field in CustomField._base_manager.filter(namespace="itambox", management_kind="core")
        }
        assert len(core_fields) == target["expected_counts"]["active_fields"]
        assert set(core_fields) == {row["key"] for row in target["active_fields"]}
        for row in target["active_fields"]:
            field = core_fields[row["key"]]
            assert field.label == row["label"]
            assert field.field_type == row["field_type"]
            assert field.lifecycle == "active"

        # The old graph's residues never reach a fresh install.
        assert not CustomField._base_manager.filter(name="input_voltage").exists()
        assert not CustomFieldChoice._base_manager.filter(key="nvme_ssd").exists()
        assert (
            CustomField._base_manager.filter(namespace="itambox", lifecycle="deprecated").count() == 0
        )

        core_sets = {
            choice_set.slug: choice_set
            for choice_set in CustomFieldChoiceSet._base_manager.filter(
                namespace="itambox", management_kind="core"
            )
        }
        assert set(core_sets) == {row["slug"] for row in target["choice_sets"]}
        for row in target["choice_sets"]:
            choices = list(
                CustomFieldChoice._base_manager.filter(choice_set_id=core_sets[row["slug"]].pk).order_by(
                    "position", "key"
                )
            )
            assert [choice.key for choice in choices] == [choice["key"] for choice in row["choices"]]

        core_fieldsets = {
            fieldset.slug
            for fieldset in CustomFieldset._base_manager.filter(namespace="itambox", management_kind="core")
        }
        assert core_fieldsets == {row["slug"] for row in target["sections"]}
        assert Category._base_manager.filter(deleted_at__isnull=True).count() == len(target["categories"])

    def test_legacy_values_survive_conversion_and_cutover(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        AssetType = old_apps.get_model("assets", "AssetType")
        Asset = old_apps.get_model("assets", "Asset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        asset_type_ct = ContentType.objects.get_or_create(app_label="assets", model="assettype")[0]
        asset_ct = ContentType.objects.get_or_create(app_label="assets", model="asset")[0]
        for key, label, field_type, targets in ADOPTION_SOURCES:
            field = CustomField.objects.create(name=key, label=label, field_type=field_type, choices="", required=False)
            field.object_types.set([asset_type_ct if target == "assettype" else asset_ct for target in targets])

        local = CustomField.objects.create(name="Rack Unit", label="Rack Unit", field_type="number")
        local.object_types.set([asset_type_ct])
        fieldset = CustomFieldset.objects.create(name="Mounting")
        fieldset.fields.add(local)

        manufacturer = Manufacturer.objects.create(name="Rehearsal Manufacturer", slug="rehearsal-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch X",
            slug="switch-x",
            custom_fieldset=fieldset,
            custom_field_data={"Rack Unit": 2, "ram_gb": "64", "unknown_legacy": {"nested": True}},
        )
        Asset.objects.create(
            name="Switch X 01",
            asset_type=asset_type,
            custom_field_data={"hostname": "sw-x-01", "Rack Unit": None},
        )

        target_executor = MigrationExecutor(connection)
        target_executor.migrate(self.migrate_to)
        new_apps = target_executor.loader.project_state(self.migrate_to).apps
        CustomField = new_apps.get_model("extras", "CustomField")
        AssetType = new_apps.get_model("assets", "AssetType")
        Asset = new_apps.get_model("assets", "Asset")
        AssetTypeFieldset = new_apps.get_model("assets", "AssetTypeFieldset")

        # Adopted and local values are preserved and converted.
        asset_type = AssetType._base_manager.get(slug="switch-x")
        assert asset_type.custom_field_data == {
            "rack_unit": "2",
            "memory_capacity": "64.000",
            "unknown_legacy": {"nested": True},
        }
        asset = Asset._base_manager.get(name="Switch X 01")
        assert asset.custom_field_data == {"hostname": "sw-x-01", "rack_unit": None}

        local_field = CustomField._base_manager.get(name="rack_unit")
        assert local_field.namespace == "local"
        assert local_field.nullable is True

        # The singular composition column is gone and the membership is dense.
        columns = {
            column.name for column in AssetType._meta.local_fields
        }
        assert "custom_fieldset" not in columns
        memberships = list(AssetTypeFieldset._base_manager.filter(asset_type_id=asset_type.pk))
        assert [membership.position for membership in memberships] == [1]

        # No residue reaches the upgraded database either.
        assert not CustomField._base_manager.filter(name="input_voltage").exists()
