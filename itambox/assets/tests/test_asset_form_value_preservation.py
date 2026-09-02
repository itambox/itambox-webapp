from django.test import TestCase

from assets.forms import AssetForm
from assets.models import Asset, AssetType, AssetTypeFieldset, Manufacturer, StatusLabel
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField


def _minimal_asset_form_data(asset, status, asset_type, **custom_values):
    data = {
        "name": asset.name,
        "asset_tag": asset.asset_tag,
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
    }
    data.update(custom_values)
    return data


class AssetFormValuePreservationTests(TestCase):
    def test_asset_update_uses_plural_composition_and_preserves_unrendered_values(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="device-details",
            label="Device details",
        )
        visible = CustomField.objects.create(
            name="test_hostname",
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
            custom_field_data={"test_hostname": "old", "hidden_device_value": "keep", "unknown": "keep"},
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
                "cf_test_hostname": "updated",
            },
            instance=asset,
        )

        self.assertIn("cf_test_hostname", form.fields)
        self.assertNotIn("cf_hidden_device_value", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(
            saved.custom_field_data,
            {"test_hostname": "updated", "hidden_device_value": "keep", "unknown": "keep"},
        )

    def test_optional_single_select_may_remain_empty_without_invalid_choice(self):
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local", slug="optional-choice", label="Optional choice"
        )
        CustomFieldChoice.objects.create(choice_set=choice_set, key="one", label="One", position=10)
        CustomField.objects.create(
            name="optional_choice",
            namespace="local",
            label="Optional choice",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            scope=CustomField.SCOPE_ASSET,
            choice_set=choice_set,
            max_values=1,
            required=False,
        )
        status = StatusLabel.objects.create(name="Available optional", slug="available-optional", type="deployable")
        manufacturer = Manufacturer.objects.create(name="Optional Manufacturer", slug="optional-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Optional Device",
            slug="optional-device",
        )
        asset = Asset.objects.create(
            name="Optional device",
            asset_tag="OPTIONAL-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={},
        )
        form = AssetForm(
            data={
                "name": asset.name,
                "asset_tag": asset.asset_tag,
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
                "cf_optional_choice": "",
            },
            instance=asset,
        )
        self.assertIn("cf_optional_choice__clear", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.custom_field_data, {})

    def test_explicit_clear_checkbox_removes_existing_value(self):
        CustomField.objects.create(
            name="clearable_value",
            namespace="local",
            label="Clearable value",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET,
        )
        status = StatusLabel.objects.create(name="Available clear", slug="available-clear", type="deployable")
        manufacturer = Manufacturer.objects.create(name="Clear Manufacturer", slug="clear-manufacturer")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Clear Device", slug="clear-device")
        asset = Asset.objects.create(
            name="Clear device",
            asset_tag="CLEAR-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={"clearable_value": "remove me"},
        )
        form = AssetForm(
            data=_minimal_asset_form_data(
                asset,
                status,
                asset_type,
                cf_clearable_value="",
                cf_clearable_value__clear="on",
            ),
            instance=asset,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertNotIn("clearable_value", saved.custom_field_data)
