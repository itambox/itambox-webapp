from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from assets.services.specification_writers import normalize_generic_asset_data
from core.importers.snipeit.common import _snipeit_choice_key, canonicalize_snipeit_custom_field_value
from core.importers.snipeit.stages.hardware import HardwareImporter


def test_generic_asset_data_requires_explicit_supported_keys_and_json_values():
    assert normalize_generic_asset_data(
        {"snipeit_id": "42", "intune_primary_user_matched": True},
        allowed_keys={"snipeit_id", "intune_primary_user_matched"},
    ) == {"snipeit_id": "42", "intune_primary_user_matched": True}

    with pytest.raises(ValidationError, match="unsupported generic asset data key"):
        normalize_generic_asset_data({"unexpected": "value"}, allowed_keys={"snipeit_id"})

    with pytest.raises(ValidationError, match="JSON value"):
        normalize_generic_asset_data({"snipeit_id": object()}, allowed_keys={"snipeit_id"})


def test_non_ui_writers_use_the_audited_writer_seam():
    root = Path(__file__).resolve().parents[2]
    hardware = (root / "core" / "importers" / "snipeit" / "stages" / "hardware.py").read_text()
    asset_models = (root / "core" / "importers" / "snipeit" / "stages" / "asset_models.py").read_text()
    seed_assets = (root / "core" / "management" / "commands" / "_seed" / "assets.py").read_text()
    intune = (root / "assets" / "tasks" / "intune_sync.py").read_text()
    writer = (root / "assets" / "services" / "specification_writers.py").read_text()
    licenses = (root / "core" / "importers" / "snipeit" / "stages" / "licenses.py").read_text()
    inventory = (root / "core" / "importers" / "snipeit" / "stages" / "inventory.py").read_text()
    catalog = (root / "core" / "importers" / "snipeit" / "stages" / "catalog.py").read_text()
    generic_stages = [
        (root / "core" / "importers" / "snipeit" / "stages" / "organization.py").read_text(),
        licenses,
        inventory,
        catalog,
    ]

    assert "merge_generic_asset_data" in hardware
    assert "apply_asset_specification_patch" in hardware
    assert ".custom_field_data.update" not in hardware
    assert "custom_field_data =" not in hardware
    assert "set_asset_type_composition" in asset_models
    assert "AssetTypeFieldset" not in asset_models
    assert ".objects.filter(asset_type" not in asset_models
    assert "apply_asset_specification_patch" in seed_assets
    assert "asset.custom_field_data =" not in seed_assets
    assert "merge_generic_asset_data" in intune
    assert "asset.custom_field_data = data" not in intune
    assert "_lock_and_authorize_generic_owner" in writer
    assert "authorize_tenant_operation" in writer
    assert "assets.specification_adapters" not in writer
    assert "load_effective_definition" in writer
    assert "resolve_access_scope" in writer
    assert "assets.specification_adapters" not in asset_models
    assert "load_prospective_definition" in asset_models
    assert "def _software_for(self, sid: int, sw_name: str, mfr, Software, tenant)" in licenses
    assert "authorize_generic_owner_scope" in inventory
    assert "authorize_generic_owner_scope" in catalog
    assert "allow_global=True" in catalog
    for source in generic_stages:
        assert "merge_generic_owner_data" in source
        assert ".custom_field_data[" not in source
        assert "custom_field_data = {" not in source


def test_snipeit_custom_field_data_rejects_unmapped_or_unsupported_entries():
    importer = HardwareImporter.__new__(HardwareImporter)
    importer.dependencies = SimpleNamespace(
        custom_fields={"known": SimpleNamespace(name="canonical_name", field_type="text", text_max_length=None)}
    )

    with pytest.raises(ValueError, match="Unmapped Snipe-IT custom field"):
        importer._custom_field_data(42, {"remote": {"field": "unknown", "value": "x"}})
    with pytest.raises(ValueError, match="Unsupported Snipe-IT custom-field entry"):
        importer._custom_field_data(42, {"remote": ["not-an-entry"]})
    with pytest.raises(ValueError, match="Unsupported Snipe-IT custom-field structure"):
        importer._custom_field_data(42, ["not-a-mapping"])


def test_snipeit_choice_transform_accepts_only_canonical_keys():
    class _Choices:
        def __init__(self):
            self.rows = [SimpleNamespace(key="in_stock", label="In stock", lifecycle="active")]

        def filter(self, **filters):
            return [row for row in self.rows if all(getattr(row, key) == value for key, value in filters.items())]

    definition = SimpleNamespace(choice_set=SimpleNamespace(choices=_Choices()))

    assert _snipeit_choice_key(definition, "in_stock") == "in_stock"
    with pytest.raises(ValidationError, match="valid choice"):
        _snipeit_choice_key(definition, "In stock")


def test_snipeit_canonicalizer_rejects_unsupported_field_types():
    with pytest.raises(ValidationError, match="Unsupported Snipe-IT custom-field type"):
        canonicalize_snipeit_custom_field_value(SimpleNamespace(field_type="quantity"), "12 kg")
