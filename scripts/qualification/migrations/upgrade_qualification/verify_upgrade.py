"""Verify data integrity, composition, constraints, and tenant isolation after
the supported upgrade.

Runs inside the normalized candidate checkout against the upgraded database.
"""

import json

from django.db import DatabaseError, connection, transaction

from assets.models import Asset, AssetType
from extras.models import CustomField, CustomFieldChoice
from organization.models import Tenant

result = {}

asset_type = AssetType.objects.get(slug="upgrade-switch")
expected_type_data = {
    "serial_number": "SN-UP-1",
    "memory_capacity": "16.000",
    "storage_capacity": "512.000",
    "poe_budget": "30.000",
}
assert asset_type.custom_field_data == expected_type_data, asset_type.custom_field_data
result["asset_type_custom_field_data"] = asset_type.custom_field_data

memberships = list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position"))
assert memberships == [("hardware", 1)], memberships
result["fieldset_memberships"] = memberships

fieldset = asset_type.fieldset_memberships.get().fieldset
assert fieldset.namespace == "local"
assert fieldset.label == "Hardware"
result["fieldset_namespace"] = fieldset.namespace

asset = Asset.objects.get(name="Upgrade Switch 01")
expected_asset_data = {"hostname": "up-01", "firmware_version": "1.2.3"}
assert asset.custom_field_data == expected_asset_data, asset.custom_field_data
result["asset_custom_field_data"] = asset.custom_field_data

# Tenant ownership survives and stays isolated.
assert asset.tenant is not None and asset.tenant.slug == "upgrade-rehearsal-tenant"
tenant_assets = Asset.objects.filter(tenant__slug="upgrade-rehearsal-tenant")
assert list(tenant_assets.values_list("name", flat=True)) == ["Upgrade Switch 01"]
assert not Asset.objects.filter(tenant__isnull=True, name="Upgrade Switch 01").exists()
result["tenant_isolation"] = "ok"

# No duplicate or lost specification values across the composition.
field_values = {key: value for key, value in asset_type.custom_field_data.items()}
assert len(field_values) == len(expected_type_data)
result["specification_value_count"] = len(field_values)

# Application-level read/write after the upgrade.
with transaction.atomic():
    asset_type.custom_field_data = {**asset_type.custom_field_data, "memory_capacity": "32.000"}
    asset_type.save(update_fields=["custom_field_data"])
asset_type.refresh_from_db()
assert asset_type.custom_field_data["memory_capacity"] == "32.000"
result["post_upgrade_write"] = asset_type.custom_field_data["memory_capacity"]

# Final vocabulary is clean; the old residues never appear.
assert not CustomField.objects.filter(name="input_voltage").exists()
assert not CustomFieldChoice.objects.filter(key="nvme_ssd").exists()
result["core_fields"] = CustomField.objects.filter(namespace="itambox", management_kind="core").count()
result["local_fields"] = CustomField.objects.filter(namespace="local").count()

# The identity guards hold on the upgraded database.
with connection.cursor() as cursor:
    try:
        cursor.execute("UPDATE extras_customfield SET name = 'renamed' WHERE namespace = 'itambox'")
        raise AssertionError("definition identity update unexpectedly succeeded")
    except DatabaseError as exc:
        assert "immutable" in str(exc), exc
result["identity_guard"] = "ok"

print("VERIFY " + json.dumps(result, sort_keys=True))
