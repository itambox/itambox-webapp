"""Migration rehearsal for the normalized #479 specification conversion.

Runs a pre-#479 database through the normalized schema and conversion
migrations and proves that legacy definition state, composition, and stored
values convert into the final specification contract without transitional
residue. Run explicitly:

    PYTHONPATH=itambox pytest scripts/qualification/migrations/
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests

MIGRATE_FROM = [
    ("assets", "0101_seed_canonical_missing_status"),
    ("extras", "0113_upgrade_legacy_webhook_retry_schedules"),
]

MIGRATE_TO = [
    ("assets", "0103_asset_type_specification_conversion"),
    ("extras", "0115_asset_type_definition_conversion"),
]

# The five legacy foundation fields that the conversion adopts into the core
# vocabulary. The preflight requires the exact pre-#479 signature, so the
# rehearsal has to create all of them or the migration fails closed.
ADOPTION_SOURCES = (
    ("ram_gb", "RAM (GB)", "number", ("assettype",)),
    ("storage_gb", "Storage (GB)", "number", ("assettype",)),
    ("poe_budget_w", "PoE Budget (Watts)", "number", ("assettype",)),
    ("hostname", "Hostname", "text", ("asset",)),
    ("firmware_version", "Firmware Version", "text", ("asset",)),
)


@isolate_migration_tests
@pytest.mark.serial_only
class NormalizedFoundationMigrationTests(IsolatedMigrationTestCase):
    migrate_from = MIGRATE_FROM
    migrate_to = MIGRATE_TO

    def _start_legacy_state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        return executor, old_apps

    def _create_legacy_vocabulary(self, old_apps, *, broken_adoption_label=None):
        AssetType = old_apps.get_model("assets", "AssetType")
        Asset = old_apps.get_model("assets", "Asset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        asset_type_ct = ContentType.objects.get_or_create(app_label="assets", model="assettype")[0]
        asset_ct = ContentType.objects.get_or_create(app_label="assets", model="asset")[0]

        for key, label, field_type, targets in ADOPTION_SOURCES:
            if key == "ram_gb" and broken_adoption_label is not None:
                label = broken_adoption_label
            field = CustomField.objects.create(name=key, label=label, field_type=field_type, choices="", required=False)
            field.object_types.set([asset_type_ct if target == "assettype" else asset_ct for target in targets])

        serial = CustomField.objects.create(name="Serial Number", label="Serial Number", field_type="text")
        serial.object_types.set([asset_type_ct, asset_ct])
        port_count = CustomField.objects.create(name="Port Count", label="Port Count", field_type="number")
        port_count.object_types.set([asset_type_ct])
        form_factor = CustomField.objects.create(
            name="Form Factor", label="Form Factor", field_type="select", choices="Rack\nTower"
        )
        form_factor.object_types.set([asset_type_ct])

        fieldset = CustomFieldset.objects.create(name="Hardware")
        fieldset.fields.add(serial)
        fieldset.fields.add(port_count)
        fieldset.fields.add(form_factor)

        manufacturer = Manufacturer.objects.create(name="Rehearsal Manufacturer", slug="rehearsal-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch X",
            slug="switch-x",
            custom_fieldset=fieldset,
            custom_field_data={
                "Serial Number": "SN-0001",
                "ram_gb": "32",
                "Port Count": 24,
                "Form Factor": "Rack",
                "unmapped_legacy_key": "keep-me",
            },
        )
        Asset.objects.create(
            name="Switch X 01",
            asset_type=asset_type,
            custom_field_data={"hostname": "sw-x-01", "Serial Number": "SN-0001"},
        )
        return asset_type

    def test_conversion_rewrites_legacy_vocabulary_composition_and_values(self):
        executor, old_apps = self._start_legacy_state()
        self._create_legacy_vocabulary(old_apps)

        target_executor = MigrationExecutor(connection)
        target_executor.migrate(self.migrate_to)
        new_apps = target_executor.loader.project_state(self.migrate_to).apps
        CustomField = new_apps.get_model("extras", "CustomField")
        CustomFieldChoice = new_apps.get_model("extras", "CustomFieldChoice")
        CustomFieldset = new_apps.get_model("extras", "CustomFieldset")
        CustomFieldsetField = new_apps.get_model("extras", "CustomFieldsetField")
        AssetType = new_apps.get_model("assets", "AssetType")
        Asset = new_apps.get_model("assets", "Asset")
        AssetTypeFieldset = new_apps.get_model("assets", "AssetTypeFieldset")

        by_name = {field.name: field for field in CustomField._base_manager.all()}

        # Local definitions keep their identity under the physical key.
        serial = by_name["serial_number"]
        assert serial.namespace == "local"
        assert serial.management_kind == "local"
        assert serial.field_type == "text"
        assert serial.label == "Serial Number"
        assert {f"{ct.app_label}.{ct.model}" for ct in serial.object_types.all()} == {
            "assets.assettype",
            "assets.asset",
        }

        # Legacy numbers become scaled decimals; the scale follows stored values.
        port_count = by_name["port_count"]
        assert port_count.field_type == "decimal"
        assert port_count.decimal_scale == 0
        assert port_count.nullable is False

        # Legacy selects become a local single-select with a generated choice set.
        form_factor = by_name["form_factor"]
        assert form_factor.field_type == "single-select"
        assert form_factor.max_values == 1
        choice_set = form_factor.choice_set
        assert choice_set.namespace == "local"
        assert choice_set.label == "Form Factor choices"
        choices = list(
            CustomFieldChoice._base_manager.filter(choice_set_id=choice_set.pk).order_by("position", "key")
        )
        # Choices are renumbered deterministically (dense positions, key order).
        assert [(choice.key, choice.label, choice.position) for choice in choices] == [
            ("rack", "Rack", 1),
            ("tower", "Tower", 2),
        ]

        # The five foundation fields are adopted into the core vocabulary.
        memory = by_name["memory_capacity"]
        assert memory.namespace == "itambox"
        assert memory.management_kind == "core"
        assert memory.field_type == "decimal"
        assert memory.decimal_scale == 3
        assert memory.quantity_kind == "digital_information"
        assert memory.canonical_unit == "GiB"
        assert memory.required is False
        # The canonical target set for memory_capacity spans Asset Types and Assets.
        assert {f"{ct.app_label}.{ct.model}" for ct in memory.object_types.all()} == {
            "assets.assettype",
            "assets.asset",
        }
        assert "ram_gb" not in by_name

        # Fieldsets become reusable definitions with ordered memberships.
        fieldset = CustomFieldset._base_manager.get(namespace="local", slug="hardware")
        assert fieldset.label == "Hardware"
        memberships = list(
            CustomFieldsetField._base_manager.filter(fieldset_id=fieldset.pk).order_by("position")
        )
        assert [membership.custom_field_id for membership in memberships] == [
            by_name["serial_number"].pk,
            by_name["port_count"].pk,
            by_name["form_factor"].pk,
        ]
        # The conversion materializes the reusable membership model and
        # renumbers it to dense positions in a deterministic order.
        assert [membership.position for membership in memberships] == [1, 2, 3]

        # The singular composition survives as an ordered reusable membership.
        asset_type = AssetType._base_manager.get(slug="switch-x")
        type_memberships = list(AssetTypeFieldset._base_manager.filter(asset_type_id=asset_type.pk))
        assert len(type_memberships) == 1
        assert type_memberships[0].fieldset_id == fieldset.pk
        assert type_memberships[0].position >= 1

        # Stored values are re-keyed, converted, and preserved.
        assert asset_type.custom_field_data == {
            "serial_number": "SN-0001",
            "memory_capacity": "32.000",
            "port_count": "24",
            "form_factor": "rack",
            "unmapped_legacy_key": "keep-me",
        }
        asset = Asset._base_manager.get(name="Switch X 01")
        assert asset.custom_field_data == {"hostname": "sw-x-01", "serial_number": "SN-0001"}

    def test_conversion_refuses_mismatched_adoption_signature(self):
        executor, old_apps = self._start_legacy_state()
        self._create_legacy_vocabulary(old_apps, broken_adoption_label="RAM")

        with self.assertRaises(RuntimeError) as caught:
            MigrationExecutor(connection).migrate(self.migrate_to)
        assert "adoption_source_signature" in str(caught.exception)

        # The conversion is atomic: nothing was partially converted.
        old_apps = executor.loader.project_state(self.migrate_from).apps
        CustomField = old_apps.get_model("extras", "CustomField")
        assert CustomField._base_manager.get(name="ram_gb").label == "RAM"
        assert not CustomField._base_manager.filter(name="memory_capacity").exists()
