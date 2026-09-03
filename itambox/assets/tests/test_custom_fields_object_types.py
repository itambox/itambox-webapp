from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from assets.customfields import resolve_asset_type_custom_fields
from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm
from assets.forms.supplier_form import SupplierForm
from assets.models import Asset, AssetRole, AssetType, AssetTypeFieldset, Category, Manufacturer, StatusLabel, Supplier
from extras.customfields import apply_custom_field_filters
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField

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
        self.status = StatusLabel.objects.get_or_create(
            slug="available", defaults={"name": "Available", "type": "deployable"}
        )[0]

        self.asset_ct = ContentType.objects.get_for_model(Asset)
        self.assettype_ct = ContentType.objects.get_for_model(AssetType)

        # Spec fields (apply to AssetType)
        self.cf_cpu = CustomField.objects.create(
            name="cpu",
            label="CPU Model",
            field_type="text",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        self.cf_cpu.object_types.add(self.assettype_ct)
        self.cf_ram = CustomField.objects.create(
            name="ram_gb",
            label="RAM (GB)",
            field_type="decimal",
            scope=CustomField.SCOPE_ASSET_TYPE,
            decimal_scale=0,
        )
        self.cf_ram.object_types.add(self.assettype_ct)

        # Per-device fields (apply to Asset)
        self.cf_test_hostname = CustomField.objects.create(
            name="test_hostname",
            label="Hostname",
            field_type="text",
            scope=CustomField.SCOPE_ASSET,
        )
        self.cf_test_hostname.object_types.add(self.asset_ct)
        self.cf_encrypted = CustomField.objects.create(
            name="encrypted",
            label="Disk Encrypted",
            field_type="boolean",
            scope=CustomField.SCOPE_ASSET,
        )
        self.cf_encrypted.object_types.add(self.asset_ct)

        self.fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="laptop-specs",
            label="Laptop Specs",
        )
        for position, field in enumerate(
            (self.cf_cpu, self.cf_ram, self.cf_test_hostname, self.cf_encrypted),
            start=1,
        ):
            CustomFieldsetField.objects.create(fieldset=self.fieldset, custom_field=field, position=position * 10)

        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Latitude 5550",
            slug="dell-latitude-5550",
            category=self.category,
            asset_role=self.role,
        )
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=self.fieldset, position=10)

    def test_object_types_assignment(self):
        self.assertIn(self.assettype_ct, self.cf_cpu.object_types.all())
        self.assertIn(self.asset_ct, self.cf_test_hostname.object_types.all())

    def test_asset_type_form_renders_only_spec_fields(self):
        form = AssetTypeForm(instance=self.asset_type)
        self.assertIn("cf_cpu", form.fields)
        self.assertIn("cf_ram_gb", form.fields)
        self.assertNotIn("cf_test_hostname", form.fields)
        self.assertNotIn("cf_encrypted", form.fields)

    def test_asset_form_renders_only_device_fields(self):
        asset = Asset.objects.create(
            name="My Laptop",
            asset_tag="TAG-1",
            asset_type=self.asset_type,
            status=self.status,
        )
        form = AssetForm(instance=asset)
        self.assertNotIn("cf_cpu", form.fields)
        self.assertNotIn("cf_ram_gb", form.fields)
        self.assertIn("cf_test_hostname", form.fields)
        self.assertIn("cf_encrypted", form.fields)

    def test_global_asset_field_shows_without_fieldset(self):
        # A field targeting Asset that belongs to no fieldset applies globally.
        cf_global = CustomField.objects.create(
            name="cost_center",
            label="Cost Center",
            field_type="text",
            scope=CustomField.SCOPE_ASSET,
        )
        cf_global.object_types.add(self.asset_ct)
        asset = Asset.objects.create(
            name="Plain Laptop",
            asset_tag="TAG-2",
            status=self.status,
        )
        form = AssetForm(instance=asset)
        self.assertIn("cf_cost_center", form.fields)
        # Fieldset-bound fields don't leak onto assets of other/no types.
        self.assertNotIn("cf_test_hostname", form.fields)

    def test_resolver_excludes_soft_deleted_definition_from_active_composition(self):
        deleted = CustomField.objects.create(
            name="deleted_spec",
            label="Deleted specification",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET_TYPE,
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
            deleted_at=timezone.now(),
        )
        CustomFieldsetField.objects.create(fieldset=self.fieldset, custom_field=deleted, position=50)

        resolved = resolve_asset_type_custom_fields(self.asset_type)

        self.assertNotIn("deleted_spec", {item.definition.name for item in resolved})

    def test_asset_type_filters_canonical_decimal_and_multi_select_values(self):
        decimal = CustomField.objects.create(
            name="exact_capacity",
            label="Exact capacity",
            field_type=CustomField.FIELD_TYPE_DECIMAL,
            scope=CustomField.SCOPE_ASSET_TYPE,
            decimal_scale=3,
        )
        decimal.object_types.add(self.assettype_ct)
        choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="protocol-filter-choices",
            label="Protocol filter choices",
        )
        for position, key in enumerate(("red", "blue"), start=1):
            CustomFieldChoice.objects.create(choice_set=choice_set, key=key, label=key.title(), position=position * 10)
        multi = CustomField.objects.create(
            name="protocols",
            label="Protocols",
            field_type=CustomField.FIELD_TYPE_MULTI_SELECT,
            scope=CustomField.SCOPE_ASSET_TYPE,
            max_values=2,
            choice_set=choice_set,
        )
        multi.object_types.add(self.assettype_ct)
        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Filtered Laptop",
            slug="filtered-laptop",
            custom_field_data={"exact_capacity": "16.000", "protocols": ["blue", "red"]},
        )

        queryset = AssetType.objects.filter(pk=asset_type.pk)

        self.assertEqual(
            list(apply_custom_field_filters(queryset, AssetType, {"cf_exact_capacity": "16"})),
            [asset_type],
        )
        self.assertEqual(
            list(apply_custom_field_filters(queryset, AssetType, {"cf_protocols": "red"})),
            [asset_type],
        )
        self.assertEqual(
            apply_custom_field_filters(queryset, AssetType, {"cf_exact_capacity": "not-a-number"}).count(),
            0,
        )


class GenericCustomFieldFormMixinTestCase(TestCase):
    """The generic mixin renders/persists custom fields for any opted-in model."""

    def test_supplier_form_roundtrip(self):
        supplier_ct = ContentType.objects.get_for_model(Supplier)
        cf = CustomField.objects.create(name="account_no", label="Account Number", field_type="text")
        cf.object_types.add(supplier_ct)

        form = SupplierForm(data={"name": "Bechtle AG", "slug": "bechtle-ag", "cf_account_no": "ACC-42"})
        self.assertTrue(form.is_valid(), form.errors)
        supplier = form.save()
        self.assertEqual(supplier.custom_field_data.get("account_no"), "ACC-42")

        # Round-trip: the stored value comes back as the form initial.
        form2 = SupplierForm(instance=supplier)
        self.assertEqual(form2.fields["cf_account_no"].initial, "ACC-42")

    def test_supplier_form_applies_definition_lifecycle_to_stored_values(self):
        supplier_ct = ContentType.objects.get_for_model(Supplier)
        active = CustomField.objects.create(name="active_note", label="Active note")
        deprecated = CustomField.objects.create(
            name="old_note",
            label="Old note",
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )
        deleted = CustomField.objects.create(
            name="deleted_note",
            label="Deleted note",
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
            deleted_at=timezone.now(),
        )
        for definition in (active, deprecated, deleted):
            definition.object_types.add(supplier_ct)
        supplier = Supplier.objects.create(
            name="Lifecycle Supplier",
            slug="lifecycle-supplier",
            custom_field_data={"old_note": "retain", "deleted_note": "preserve"},
        )

        edit_form = SupplierForm(instance=supplier)
        self.assertIn("cf_active_note", edit_form.fields)
        self.assertIn("cf_old_note", edit_form.fields)
        self.assertTrue(edit_form.fields["cf_old_note"].disabled)
        self.assertNotIn("cf_deleted_note", edit_form.fields)

        bound_form = SupplierForm(
            data={
                "name": supplier.name,
                "slug": supplier.slug,
                "cf_active_note": "updated",
            },
            instance=supplier,
        )
        self.assertTrue(bound_form.is_valid(), bound_form.errors)
        saved = bound_form.save()
        self.assertEqual(
            saved.custom_field_data,
            {
                "active_note": "updated",
                "old_note": "retain",
                "deleted_note": "preserve",
            },
        )

        create_form = SupplierForm()
        self.assertIn("cf_active_note", create_form.fields)
        self.assertNotIn("cf_old_note", create_form.fields)
        self.assertNotIn("cf_deleted_note", create_form.fields)

    def test_supplier_filters_resolve_only_active_definitions(self):
        supplier_ct = ContentType.objects.get_for_model(Supplier)
        active = CustomField.objects.create(name="region", label="Region")
        deprecated = CustomField.objects.create(
            name="old_region",
            label="Old region",
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )
        deleted = CustomField.objects.create(
            name="deleted_region",
            label="Deleted region",
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
            deleted_at=timezone.now(),
        )
        for definition in (active, deprecated, deleted):
            definition.object_types.add(supplier_ct)
        first = Supplier.objects.create(
            name="First Supplier",
            slug="first-supplier",
            custom_field_data={"region": "north", "old_region": "legacy", "deleted_region": "secret"},
        )
        second = Supplier.objects.create(
            name="Second Supplier",
            slug="second-supplier",
            custom_field_data={"region": "south"},
        )
        queryset = Supplier.objects.order_by("pk")

        self.assertEqual(
            list(apply_custom_field_filters(queryset, Supplier, {"cf_region": "north"})),
            [first],
        )
        self.assertEqual(
            list(apply_custom_field_filters(queryset, Supplier, {"cf_old_region": "legacy"})),
            [first, second],
        )
        self.assertEqual(
            list(apply_custom_field_filters(queryset, Supplier, {"cf_deleted_region": "secret"})),
            [first, second],
        )
