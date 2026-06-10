from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.contrib.auth import get_user_model

from assets.models import Asset, AssetType, Manufacturer, Category, AssetRole, StatusLabel
from extras.models import CustomField, CustomFieldset
from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm

User = get_user_model()


class CustomFieldsObjectTypesTestCase(TestCase):
    """Custom fields declare applicability via object_types: fields targeting
    AssetType act as hardware specs; fields targeting Asset are per-device
    details. Fieldsets group fields per asset type; fields outside any
    fieldset apply globally."""

    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.category = Category.objects.create(name="Laptops", slug="laptops", applies_to={"asset": True})
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status = StatusLabel.objects.get_or_create(slug="available", defaults={"name": "Available", "type": "deployable"})[0]

        self.asset_ct = ContentType.objects.get_for_model(Asset)
        self.assettype_ct = ContentType.objects.get_for_model(AssetType)

        # Spec fields (apply to AssetType)
        self.cf_cpu = CustomField.objects.create(name="cpu", label="CPU Model", field_type="text")
        self.cf_cpu.object_types.add(self.assettype_ct)
        self.cf_ram = CustomField.objects.create(name="ram_gb", label="RAM (GB)", field_type="number")
        self.cf_ram.object_types.add(self.assettype_ct)

        # Per-device fields (apply to Asset)
        self.cf_hostname = CustomField.objects.create(name="hostname", label="Hostname", field_type="text")
        self.cf_hostname.object_types.add(self.asset_ct)
        self.cf_encrypted = CustomField.objects.create(name="encrypted", label="Disk Encrypted", field_type="boolean")
        self.cf_encrypted.object_types.add(self.asset_ct)

        self.fieldset = CustomFieldset.objects.create(name="Laptop Specs")
        self.fieldset.fields.add(self.cf_cpu, self.cf_ram, self.cf_hostname, self.cf_encrypted)

        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5550",
            slug="dell-latitude-5550",
            category=self.category,
            asset_role=self.role,
            custom_fieldset=self.fieldset,
        )

    def test_object_types_assignment(self):
        self.assertIn(self.assettype_ct, self.cf_cpu.object_types.all())
        self.assertIn(self.asset_ct, self.cf_hostname.object_types.all())

    def test_asset_type_form_renders_only_spec_fields(self):
        form = AssetTypeForm(instance=self.asset_type)
        self.assertIn("cf_cpu", form.fields)
        self.assertIn("cf_ram_gb", form.fields)
        self.assertNotIn("cf_hostname", form.fields)
        self.assertNotIn("cf_encrypted", form.fields)

    def test_asset_form_renders_only_device_fields(self):
        asset = Asset.objects.create(
            name="My Laptop", asset_tag="TAG-1",
            asset_type=self.asset_type, status=self.status,
        )
        form = AssetForm(instance=asset)
        self.assertNotIn("cf_cpu", form.fields)
        self.assertNotIn("cf_ram_gb", form.fields)
        self.assertIn("cf_hostname", form.fields)
        self.assertIn("cf_encrypted", form.fields)

    def test_global_asset_field_shows_without_fieldset(self):
        # A field targeting Asset that belongs to no fieldset applies globally.
        cf_global = CustomField.objects.create(name="cost_center", label="Cost Center", field_type="text")
        cf_global.object_types.add(self.asset_ct)
        asset = Asset.objects.create(
            name="Plain Laptop", asset_tag="TAG-2", status=self.status,
        )
        form = AssetForm(instance=asset)
        self.assertIn("cf_cost_center", form.fields)
        # Fieldset-bound fields don't leak onto assets of other/no types.
        self.assertNotIn("cf_hostname", form.fields)


class GenericCustomFieldFormMixinTestCase(TestCase):
    """The generic mixin renders/persists custom fields for any opted-in model."""

    def test_supplier_form_roundtrip(self):
        from assets.models import Supplier
        from assets.forms.supplier_form import SupplierForm

        supplier_ct = ContentType.objects.get_for_model(Supplier)
        cf = CustomField.objects.create(name="account_no", label="Account Number", field_type="text")
        cf.object_types.add(supplier_ct)

        form = SupplierForm(data={'name': 'Bechtle AG', 'slug': 'bechtle-ag', 'cf_account_no': 'ACC-42'})
        self.assertTrue(form.is_valid(), form.errors)
        supplier = form.save()
        self.assertEqual(supplier.custom_field_data.get('account_no'), 'ACC-42')

        # Round-trip: the stored value comes back as the form initial.
        form2 = SupplierForm(instance=supplier)
        self.assertEqual(form2.fields['cf_account_no'].initial, 'ACC-42')
