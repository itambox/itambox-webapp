"""Migration rehearsal for the normalized #479 provenance capture and guards.

Proves that the normalized chain archives the legacy evidence state for every
converted definition, that library-managed ownership stays empty, and that the
final identity guards hold after the cutover. Run explicitly:

    PYTHONPATH=itambox pytest scripts/qualification/migrations/
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError
from django.utils import timezone

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
class NormalizedProvenanceMigrationTests(IsolatedMigrationTestCase):
    migrate_from = MIGRATE_FROM
    migrate_to = MIGRATE_TO

    def test_provenance_capture_archives_every_converted_definition(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        AssetType = old_apps.get_model("assets", "AssetType")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        asset_type_ct = ContentType.objects.get_or_create(app_label="assets", model="assettype")[0]
        asset_ct = ContentType.objects.get_or_create(app_label="assets", model="asset")[0]
        for key, label, field_type, targets in ADOPTION_SOURCES:
            field = CustomField.objects.create(name=key, label=label, field_type=field_type, choices="", required=False)
            field.object_types.set([asset_type_ct if target == "assettype" else asset_ct for target in targets])

        retired = CustomField.objects.create(name="Old Port Count", label="Old Port Count", field_type="number")
        retired.object_types.set([asset_type_ct])
        retired.deleted_at = timezone.now()
        retired.save(update_fields=["deleted_at"])

        fieldset = CustomFieldset.objects.create(name="Hardware")
        fieldset.fields.add(retired)

        manufacturer = Manufacturer.objects.create(name="Rehearsal Manufacturer", slug="rehearsal-manufacturer")
        AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch X",
            slug="switch-x",
            custom_fieldset=fieldset,
        )
        AssetType.objects.create(manufacturer=manufacturer, model="Switch Y", slug="switch-y")

        target_executor = MigrationExecutor(connection)
        target_executor.migrate(self.migrate_to)
        new_apps = target_executor.loader.project_state(self.migrate_to).apps
        CustomField = new_apps.get_model("extras", "CustomField")
        CustomFieldChoice = new_apps.get_model("extras", "CustomFieldChoice")
        CustomFieldChoiceSet = new_apps.get_model("extras", "CustomFieldChoiceSet")
        CustomFieldset = new_apps.get_model("extras", "CustomFieldset")
        AssetType = new_apps.get_model("assets", "AssetType")
        LegacyProvenance = new_apps.get_model("extras", "SpecificationLibraryLegacyProvenance")
        SpecificationLibrary = new_apps.get_model("extras", "SpecificationLibrary")
        SpecificationLibraryRelease = new_apps.get_model("extras", "SpecificationLibraryRelease")

        # No library-managed ownership exists in a pre-#479 upgrade.
        assert not SpecificationLibrary._base_manager.exists()
        assert not SpecificationLibraryRelease._base_manager.exists()

        # Every converted definition, choice, and asset type is archived once.
        expected_counts = {
            "asset_type": AssetType._base_manager.count(),
            "custom_field": CustomField._base_manager.count(),
            "custom_fieldset": CustomFieldset._base_manager.count(),
            "choice_set": CustomFieldChoiceSet._base_manager.count(),
            "choice": CustomFieldChoice._base_manager.count(),
        }
        # The seeded core vocabulary coexists with the rehearsal objects; the
        # provenance loop below is the actual invariant check.
        assert expected_counts["asset_type"] == 2
        assert expected_counts["custom_fieldset"] >= 1
        assert expected_counts["custom_field"] >= 1
        for owner_kind, expected in expected_counts.items():
            rows = list(LegacyProvenance._base_manager.filter(owner_kind=owner_kind))
            assert len(rows) == expected, owner_kind
            assert {row.disposition for row in rows} == {"uninitialized"}
            assert {row.library_id for row in rows} == {None}
            assert {row.legacy_release for row in rows} == {None}

        # The archive freezes the owner namespace, including the retired row.
        by_owner = {
            (row.owner_kind, row.owner_id): row for row in LegacyProvenance._base_manager.all()
        }
        retired_field = CustomField._base_manager.get(name="old_port_count")
        assert by_owner[("custom_field", retired_field.pk)].owner_namespace == "local"
        adopted = CustomField._base_manager.get(name="hostname")
        assert by_owner[("custom_field", adopted.pk)].owner_namespace == "itambox"
        fieldset = CustomFieldset._base_manager.get(slug="hardware")
        assert by_owner[("custom_fieldset", fieldset.pk)].owner_namespace == "local"
        asset_type = AssetType._base_manager.get(slug="switch-x")
        assert by_owner[("asset_type", asset_type.pk)].owner_namespace == ""

    def test_final_guards_protect_definition_identities(self):
        target_executor = MigrationExecutor(connection)
        target_executor.migrate(self.migrate_to)
        new_apps = target_executor.loader.project_state(self.migrate_to).apps
        CustomField = new_apps.get_model("extras", "CustomField")
        CustomFieldChoice = new_apps.get_model("extras", "CustomFieldChoice")
        AssetType = new_apps.get_model("assets", "AssetType")
        Manufacturer = new_apps.get_model("assets", "Manufacturer")

        field = CustomField._base_manager.get(namespace="itambox", name="hostname")
        with self.assertRaises(DatabaseError) as caught:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE extras_customfield SET name = 'renamed_hostname' WHERE id = %s", [field.pk])
        assert "immutable" in str(caught.exception)

        with self.assertRaises(DatabaseError) as caught:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM extras_customfield WHERE id = %s", [field.pk])
        assert "permanent" in str(caught.exception)

        choice = CustomFieldChoice._base_manager.filter(key="hdd").first()
        with self.assertRaises(DatabaseError) as caught:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE extras_customfieldchoice SET key = 'hdd_renamed' WHERE id = %s", [choice.pk])
        assert "immutable" in str(caught.exception)

        manufacturer = Manufacturer._base_manager.create(name="Guard Manufacturer", slug="guard-manufacturer")
        asset_type = AssetType._base_manager.create(manufacturer=manufacturer, model="Guard Model", slug="guard-model")
        with self.assertRaises(DatabaseError) as caught:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE assets_assettype SET connector_identity = 'guard-connector' WHERE id = %s",
                    [asset_type.pk],
                )
        assert "immutable" in str(caught.exception)
