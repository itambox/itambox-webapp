from django.test import TestCase

from assets.forms import AssetForm
from assets.models import Asset, AssetType, AssetTypeFieldset, Manufacturer, StatusLabel
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


class AssetFormValuePreservationTests(TestCase):
    def test_asset_update_uses_plural_composition_and_preserves_unrendered_values(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        fieldset = CustomFieldset.objects.create(
            name="Device details",
            namespace="local",
            slug="device-details",
            label="Device details",
        )
        visible = CustomField.objects.create(
            name="hostname",
            namespace="local",
            label="Hostname",
            scope=CustomField.SCOPE_ASSET,
        )
        hidden = CustomField.objects.create(
            name="hidden_device_value",
            namespace="local",
            label="Hidden device value",
            scope=CustomField.SCOPE_ASSET,
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=visible, position=10)
        hidden_fieldset = CustomFieldset.objects.create(
            name="Hidden details",
            namespace="local",
            slug="hidden-details",
            label="Hidden details",
        )
        CustomFieldsetField.objects.create(fieldset=hidden_fieldset, custom_field=hidden, position=10)
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Device", slug="example-device")
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)
        status = StatusLabel.objects.create(name="Available 479", slug="available-479", type="deployable")
        asset = Asset.objects.create(
            name="Device 1",
            asset_tag="DEVICE-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={"hostname": "old", "hidden_device_value": "keep", "unknown": "keep"},
        )

        form = AssetForm(
            data={
                "name": "Device 1",
                "asset_tag": "DEVICE-1",
                "serial_number": "",
                "asset_type": asset_type.pk,
                "asset_role": "",
                "status": status.pk,
                "location": "",
                "tenant": "",
                "purchase_date": "",
                "purchase_cost": "",
                "salvage_value": "",
                "currency": "EUR",
                "order_number": "",
                "supplier": "",
                "purchase_order_line": "",
                "cost_center": "",
                "in_service_date": "",
                "depreciation_override": "",
                "notes": "",
                "requestable": "",
                "cf_hostname": "updated",
            },
            instance=asset,
        )

        self.assertIn("cf_hostname", form.fields)
        self.assertNotIn("cf_hidden_device_value", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(
            saved.custom_field_data,
            {"hostname": "updated", "hidden_device_value": "keep", "unknown": "keep"},
        )
