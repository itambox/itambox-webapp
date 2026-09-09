"""assets/tests/test_issue479_foundation_migrations.py (migration rehearsals live under scripts/qualification/migrations/)."""

import importlib
from types import SimpleNamespace

import pytest
from django.test import SimpleTestCase


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
                [field], {"ram_gb": migration._expected_adoption_signature("ram_gb")}, {"ram_gb": "memory_capacity"}
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
            signatures[source_key] = migration._signature(field, [f"{content_type.app_label}.{content_type.model}"])
            key_map[source_key] = migration.ADOPTION_FIELDS[source_key]["name"]
        before = [(field.name, field.label, field.field_type) for field in fields]
        with pytest.raises(RuntimeError, match="adoption_source_signature"):
            migration._preflight_adoption_sources(fields, signatures, key_map)
        self.assertEqual([(field.name, field.label, field.field_type) for field in fields], before)
