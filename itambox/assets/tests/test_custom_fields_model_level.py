from django.test import TestCase
from django.contrib.auth import get_user_model
from assets.models import Asset, AssetType, Manufacturer, Category, AssetRole, StatusLabel
from extras.models import CustomField, CustomFieldset
from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm

User = get_user_model()

class CustomFieldsModelLevelTestCase(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.category = Category.objects.create(name="Laptops", slug="laptops", applies_to={"asset": True})
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status = StatusLabel.objects.get_or_create(slug="available", defaults={"name": "Available", "type": "deployable"})[0]

        # Create model-level custom fields
        self.cf_cpu = CustomField.objects.create(
            name="cpu",
            label="CPU Model",
            field_type="text",
            model_level=True
        )
        self.cf_ram = CustomField.objects.create(
            name="ram_gb",
            label="RAM (GB)",
            field_type="number",
            model_level=True
        )

        # Create instance-level custom fields
        self.cf_hostname = CustomField.objects.create(
            name="hostname",
            label="Hostname",
            field_type="text",
            model_level=False
        )
        self.cf_encrypted = CustomField.objects.create(
            name="encrypted",
            label="Disk Encrypted",
            field_type="boolean",
            model_level=False
        )

        # Create Custom Fieldset
        self.fieldset = CustomFieldset.objects.create(name="Laptop Specs")
        self.fieldset.fields.add(self.cf_cpu, self.cf_ram, self.cf_hostname, self.cf_encrypted)

        # Create Asset Type
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5550",
            slug="dell-latitude-5550",
            category=self.category,
            asset_role=self.role,
            custom_fieldset=self.fieldset
        )

    def test_custom_field_model_level_attributes(self):
        self.assertTrue(self.cf_cpu.model_level)
        self.assertTrue(self.cf_ram.model_level)
        self.assertFalse(self.cf_hostname.model_level)
        self.assertFalse(self.cf_encrypted.model_level)

    def test_asset_type_form_renders_only_model_level_fields(self):
        form = AssetTypeForm(instance=self.asset_type)
        # Should render 'cf_cpu' and 'cf_ram_gb'
        self.assertIn("cf_cpu", form.fields)
        self.assertIn("cf_ram_gb", form.fields)
        # Should NOT render 'cf_hostname' and 'cf_encrypted'
        self.assertNotIn("cf_hostname", form.fields)
        self.assertNotIn("cf_encrypted", form.fields)

    def test_asset_form_renders_only_instance_level_fields(self):
        asset = Asset.objects.create(
            name="My Laptop",
            asset_tag="TAG-1",
            asset_type=self.asset_type,
            status=self.status
        )
        form = AssetForm(instance=asset)
        # Should NOT render 'cf_cpu' and 'cf_ram_gb'
        self.assertNotIn("cf_cpu", form.fields)
        self.assertNotIn("cf_ram_gb", form.fields)
        # Should render 'cf_hostname' and 'cf_encrypted'
        self.assertIn("cf_hostname", form.fields)
        self.assertIn("cf_encrypted", form.fields)
