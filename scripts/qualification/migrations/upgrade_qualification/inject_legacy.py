"""Inject representative pre-#479 data into a predecessor database.

Runs inside the predecessor checkout, so every row is created through the
pre-#479 model state that a supported upgrade starts from.
"""

from django.contrib.contenttypes.models import ContentType

from assets.models import Asset, AssetType, Manufacturer
from extras.models import CustomField, CustomFieldset
from organization.models import Tenant

asset_type_ct = ContentType.objects.get(app_label="assets", model="assettype")
asset_ct = ContentType.objects.get(app_label="assets", model="asset")

sources = (
    ("ram_gb", "RAM (GB)", "number", asset_type_ct),
    ("storage_gb", "Storage (GB)", "number", asset_type_ct),
    ("poe_budget_w", "PoE Budget (Watts)", "number", asset_type_ct),
    ("hostname", "Hostname", "text", asset_ct),
    ("firmware_version", "Firmware Version", "text", asset_ct),
)
for key, label, field_type, content_type in sources:
    field = CustomField.objects.create(name=key, label=label, field_type=field_type, choices="", required=False)
    field.object_types.add(content_type)

serial = CustomField.objects.create(name="Serial Number", label="Serial Number", field_type="text")
serial.object_types.add(asset_type_ct, asset_ct)

fieldset = CustomFieldset.objects.create(name="Hardware")
fieldset.fields.add(serial)

tenant = Tenant.objects.create(name="Upgrade Rehearsal Tenant", slug="upgrade-rehearsal-tenant")

manufacturer = Manufacturer.objects.create(name="Upgrade Rehearsal Manufacturer", slug="upgrade-rehearsal-manufacturer")
asset_type = AssetType.objects.create(
    manufacturer=manufacturer,
    model="Upgrade Switch",
    slug="upgrade-switch",
    custom_fieldset=fieldset,
    custom_field_data={
        "Serial Number": "SN-UP-1",
        "ram_gb": "16",
        "storage_gb": "512",
        "poe_budget_w": "30",
    },
)
Asset.objects.create(
    name="Upgrade Switch 01",
    asset_type=asset_type,
    tenant=tenant,
    custom_field_data={"hostname": "up-01", "firmware_version": "1.2.3"},
)

print(f"INJECTED asset_type={asset_type.pk} tenant={tenant.pk} fields={CustomField.objects.count()}")
