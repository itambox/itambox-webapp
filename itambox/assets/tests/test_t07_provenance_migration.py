"""Supported predecessor-to-current T07 provenance migration rehearsal."""

from datetime import datetime, timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests


MIGRATE_FROM = [
    ("assets", "0114_issue479_t06_composition_schema"),
    ("extras", "0118_issue479_t06_definition_schema"),
]
MIGRATE_TO = [
    ("assets", "0115_issue479_t07_provenance_bridge"),
    ("extras", "0120_issue479_t07_provenance_cutover"),
]

LEGACY_LIBRARY_CHECKSUM = "sha256:" + "a" * 64
LEGACY_TYPE_CHECKSUM = "sha256:" + "b" * 64
FIELD_CHECKSUM = "sha256:" + "c" * 64
FIELDSET_CHECKSUM = "sha256:" + "d" * 64
CHOICE_SET_CHECKSUM = "sha256:" + "e" * 64
CHOICE_CHECKSUM = "sha256:" + "f" * 64
TYPE_CONNECTOR_IDENTITY = "sha256:9e5208a35f4d324cf2bf697b39e4fe34b036b60cc70ffde2b7b9d2236dab2aae"

LOCAL_TYPE_PATHS = {
    "snipeit": {"source_url": "https://snipe.example", "source_id": "107"},
    "operator": {"note": "retain", "priority": 3},
    "archive": {"legacy": ["one", "two"]},
}
LIBRARY_TYPE_PATHS = {"archive": {"source": "library-baseline", "retain": True}}


@isolate_migration_tests
@pytest.mark.serial_only
class T07ProvenanceMigrationTests(IsolatedMigrationTestCase):
    migrate_from = MIGRATE_FROM
    migrate_to = MIGRATE_TO

    def test_forward_preserves_ids_values_composition_tokens_and_archive(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        AssetTypeLibrary = old_apps.get_model("assets", "AssetTypeLibrary")
        AssetType = old_apps.get_model("assets", "AssetType")
        AssetTypeFieldset = old_apps.get_model("assets", "AssetTypeFieldset")
        Manufacturer = old_apps.get_model("assets", "Manufacturer")
        CustomField = old_apps.get_model("extras", "CustomField")
        CustomFieldChoice = old_apps.get_model("extras", "CustomFieldChoice")
        CustomFieldChoiceSet = old_apps.get_model("extras", "CustomFieldChoiceSet")
        CustomFieldset = old_apps.get_model("extras", "CustomFieldset")
        CustomFieldsetField = old_apps.get_model("extras", "CustomFieldsetField")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        reconciled_at = datetime(2026, 9, 6, 12, 34, 56, 789012, tzinfo=timezone.utc)
        legacy_paths = {"provider": {"name": "snipeit"}, "operator": {"keep": "library"}}
        legacy_library = AssetTypeLibrary.objects.create(
            pk=700,
            namespace="legacy-stage",
            release="2026.09",
            source_checksum=LEGACY_LIBRARY_CHECKSUM,
            managed_paths=legacy_paths,
            last_reconciled_at=reconciled_at,
        )
        empty_library = AssetTypeLibrary.objects.create(
            pk=701,
            namespace="empty-stage",
            release="",
            source_checksum=None,
            managed_paths={},
            last_reconciled_at=None,
        )

        manufacturer = Manufacturer.objects.create(pk=1700, name="T07 Migration Manufacturer", slug="t07-migration")
        fieldset = CustomFieldset.objects.create(
            pk=2700,
            namespace="local",
            slug="t07-migration-fieldset",
            label="T07 Migration Fieldset",
            description="Historical composition that must survive.",
            management_kind="local",
            version=1,
            lifecycle="active",
            managed_paths={"fieldset": {"retain": True}},
            source_checksum=FIELDSET_CHECKSUM,
            last_reconciled_at=reconciled_at,
        )
        field = CustomField.objects.create(
            pk=2701,
            name="t07_migration_field",
            namespace="local",
            label="T07 Migration Field",
            help_text="Historical field value.",
            field_type="text",
            activation="composed",
            management_kind="local",
            version=1,
            lifecycle="active",
            managed_paths={"field": {"retain": "metadata"}},
            source_checksum=FIELD_CHECKSUM,
            last_reconciled_at=reconciled_at,
        )
        asset_type_ct = ContentType.objects.get(app_label="assets", model="assettype")
        field.object_types.add(asset_type_ct)
        CustomFieldsetField.objects.create(fieldset_id=fieldset.pk, custom_field_id=field.pk, position=4)

        choice_set = CustomFieldChoiceSet.objects.create(
            pk=2702,
            namespace="local",
            slug="t07-migration-choice-set",
            label="T07 Migration Choice Set",
            management_kind="local",
            version=1,
            lifecycle="active",
            managed_paths={"choice_set": {"retain": "metadata"}},
            source_checksum=CHOICE_SET_CHECKSUM,
            last_reconciled_at=reconciled_at,
        )
        choice = CustomFieldChoice.objects.create(
            pk=2703,
            choice_set_id=choice_set.pk,
            key="historical_value",
            label="Historical Value",
            position=1,
            management_kind="local",
            version=1,
            lifecycle="active",
            managed_paths={"choice": {"retain": "metadata"}},
            source_checksum=CHOICE_CHECKSUM,
            last_reconciled_at=reconciled_at,
        )

        local_type = AssetType.objects.create(
            pk=1701,
            manufacturer_id=manufacturer.pk,
            model="Imported Local Type",
            slug="imported-local-type",
            management_kind="local",
            custom_field_data={"memory_capacity": "16.000", "enabled": False, "operator_note": "retain"},
            managed_paths=LOCAL_TYPE_PATHS,
        )
        library_type = AssetType.objects.create(
            pk=1702,
            manufacturer_id=manufacturer.pk,
            model="Imported Library Type",
            slug="imported-library-type",
            management_kind="library",
            library_id=legacy_library.pk,
            library_definition_key="imported-library-type",
            library_release="2026.09",
            source_checksum=LEGACY_TYPE_CHECKSUM,
            managed_paths=LIBRARY_TYPE_PATHS,
            custom_field_data={"memory_capacity": "32.000", "operator_note": "retain"},
        )
        AssetTypeFieldset.objects.create(asset_type_id=local_type.pk, fieldset_id=fieldset.pk, position=7)
        AssetTypeFieldset.objects.create(asset_type_id=library_type.pk, fieldset_id=fieldset.pk, position=9)

        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewAssetType = new_apps.get_model("assets", "AssetType")
        NewAssetTypeFieldset = new_apps.get_model("assets", "AssetTypeFieldset")
        NewCustomField = new_apps.get_model("extras", "CustomField")
        NewCustomFieldChoice = new_apps.get_model("extras", "CustomFieldChoice")
        NewCustomFieldChoiceSet = new_apps.get_model("extras", "CustomFieldChoiceSet")
        NewCustomFieldset = new_apps.get_model("extras", "CustomFieldset")
        SpecificationLibrary = new_apps.get_model("extras", "SpecificationLibrary")
        SpecificationLibraryRelease = new_apps.get_model("extras", "SpecificationLibraryRelease")
        LegacyProvenance = new_apps.get_model("extras", "SpecificationLibraryLegacyProvenance")

        migrated_local = NewAssetType.objects.get(pk=local_type.pk)
        self.assertEqual(migrated_local.model, "Imported Local Type")
        self.assertEqual(
            migrated_local.custom_field_data,
            {"memory_capacity": "16.000", "enabled": False, "operator_note": "retain"},
        )
        self.assertEqual(migrated_local.connector_identity, TYPE_CONNECTOR_IDENTITY)
        self.assertEqual(
            list(NewAssetTypeFieldset.objects.filter(asset_type_id=local_type.pk).values_list("fieldset_id", "position")),
            [(fieldset.pk, 7)],
        )

        migrated_library = NewAssetType.objects.get(pk=library_type.pk)
        self.assertEqual(migrated_library.library_id, legacy_library.pk)
        self.assertEqual(migrated_library.library_definition_key, "imported-library-type")
        self.assertIsNone(migrated_library.connector_identity)
        self.assertEqual(
            migrated_library.custom_field_data,
            {"memory_capacity": "32.000", "operator_note": "retain"},
        )
        self.assertEqual(
            list(NewAssetTypeFieldset.objects.filter(asset_type_id=library_type.pk).values_list("fieldset_id", "position")),
            [(fieldset.pk, 9)],
        )

        migrated_field = NewCustomField.objects.get(pk=field.pk)
        migrated_fieldset = NewCustomFieldset.objects.get(pk=fieldset.pk)
        migrated_choice_set = NewCustomFieldChoiceSet.objects.get(pk=choice_set.pk)
        migrated_choice = NewCustomFieldChoice.objects.get(pk=choice.pk)
        self.assertEqual(migrated_field.connector_identity, FIELD_CHECKSUM)
        self.assertEqual(migrated_fieldset.connector_identity, FIELDSET_CHECKSUM)
        self.assertEqual(migrated_choice_set.connector_identity, CHOICE_SET_CHECKSUM)
        self.assertEqual(migrated_choice.choice_set_id, choice_set.pk)
        self.assertEqual(
            list(migrated_fieldset.field_memberships.values_list("custom_field_id", "position")),
            [(field.pk, 4)],
        )
        current_choice_fields = {field.name for field in NewCustomFieldChoice._meta.concrete_fields}
        self.assertNotIn("management_kind", current_choice_fields)
        self.assertNotIn("managed_paths", current_choice_fields)
        self.assertNotIn("source_checksum", current_choice_fields)
        self.assertNotIn("last_reconciled_at", current_choice_fields)

        migrated_library = SpecificationLibrary.objects.get(pk=legacy_library.pk)
        self.assertEqual(migrated_library.namespace, "legacy-stage")
        self.assertIsNone(migrated_library.accepted_release_id)
        self.assertEqual(SpecificationLibraryRelease.objects.count(), 0)
        self.assertEqual(SpecificationLibrary.objects.get(pk=empty_library.pk).namespace, "empty-stage")

        def archive(owner_kind, owner_id):
            return LegacyProvenance.objects.get(owner_kind=owner_kind, owner_id=owner_id)

        archived_library = archive("library", legacy_library.pk)
        self.assertEqual(archived_library.legacy_release, "2026.09")
        self.assertEqual(archived_library.legacy_source_checksum, LEGACY_LIBRARY_CHECKSUM)
        self.assertEqual(archived_library.legacy_managed_paths, legacy_paths)
        self.assertEqual(archived_library.disposition, "unreconciled")
        self.assertEqual(archive("library", empty_library.pk).disposition, "uninitialized")

        archived_type = archive("asset_type", local_type.pk)
        self.assertEqual(archived_type.legacy_managed_paths, LOCAL_TYPE_PATHS)
        self.assertEqual(archived_type.disposition, "unreconciled")
        archived_library_type = archive("asset_type", library_type.pk)
        self.assertEqual(archived_library_type.legacy_release, "2026.09")
        self.assertEqual(archived_library_type.legacy_source_checksum, LEGACY_TYPE_CHECKSUM)
        self.assertEqual(archived_library_type.legacy_managed_paths, LIBRARY_TYPE_PATHS)
        self.assertEqual(archived_library_type.disposition, "unreconciled")

        for owner_kind, owner_id, checksum, managed_paths in (
            ("custom_field", field.pk, FIELD_CHECKSUM, {"field": {"retain": "metadata"}}),
            ("custom_fieldset", fieldset.pk, FIELDSET_CHECKSUM, {"fieldset": {"retain": True}}),
            ("choice_set", choice_set.pk, CHOICE_SET_CHECKSUM, {"choice_set": {"retain": "metadata"}}),
            ("choice", choice.pk, CHOICE_CHECKSUM, {"choice": {"retain": "metadata"}}),
        ):
            with self.subTest(owner_kind=owner_kind):
                archived = archive(owner_kind, owner_id)
                self.assertEqual(archived.legacy_source_checksum, checksum)
                self.assertEqual(archived.legacy_managed_paths, managed_paths)
                self.assertEqual(archived.disposition, "unreconciled")
