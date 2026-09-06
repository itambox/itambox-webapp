from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from assets.forms import AssetForm
from assets.models import Asset, AssetTagSequence, AssetType, AssetTypeFieldset, Manufacturer, StatusLabel
from assets.services.specifications._command_support import load_effective_definition
from assets.specification_adapters import current_specification_plan
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from organization.models import Role, Tenant

User = get_user_model()


def _minimal_asset_form_data(asset, status, asset_type, **custom_values):
    data = {
        "name": asset.name,
        "asset_tag": asset.asset_tag,
        "serial_number": "",
        "asset_type": asset_type.pk,
        "asset_role": "",
        "status": status.pk,
        "location": "",
        "tenant": asset.tenant_id or "",
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


class AssetFormValuePreservationTests(TenantTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.setup_tenant_context(
            name="Value preservation",
            slug="value-preservation",
            permissions=["assets.add_asset", "assets.view_asset", "assets.change_asset"],
        )
        self.enterContext(self.tenant_context(self.tenant))
        self.request = RequestFactory().post("/assets/")
        self.request.user = self.tenant_user
        self.request.tenant = self.tenant

    def test_asset_update_uses_plural_composition_and_preserves_unrendered_values(self):
        manufacturer = Manufacturer.objects.create(name="Example", slug="example")
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug="device-details",
            label="Device details",
        )
        asset_ct = ContentType.objects.get_for_model(Asset)
        visible = CustomField.objects.create(
            name="test_hostname",
            namespace="local",
            label="Hostname",
            activation=CustomField.ACTIVATION_COMPOSED,
        )
        visible.object_types.add(asset_ct)
        hidden = CustomField.objects.create(
            name="hidden_device_value",
            namespace="local",
            label="Hidden device value",
            activation=CustomField.ACTIVATION_COMPOSED,
        )
        hidden.object_types.add(asset_ct)
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
            tenant=self.tenant,
            name="Device 1",
            asset_tag="DEVICE-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={"test_hostname": "old", "hidden_device_value": "keep", "unknown": "keep"},
        )

        form = AssetForm(
            request=self.request,
            data={
                "name": "Device 1",
                "asset_tag": "DEVICE-1",
                "serial_number": "",
                "asset_type": asset_type.pk,
                "asset_role": "",
                "status": status.pk,
                "location": "",
                "tenant": self.tenant.pk,
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
        optional = CustomField.objects.create(
            name="optional_choice",
            namespace="local",
            label="Optional choice",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=choice_set,
            max_values=1,
            required=False,
        )
        optional.object_types.add(ContentType.objects.get_for_model(Asset))
        status = StatusLabel.objects.create(name="Available optional", slug="available-optional", type="deployable")
        manufacturer = Manufacturer.objects.create(name="Optional Manufacturer", slug="optional-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Optional Device",
            slug="optional-device",
        )
        asset = Asset.objects.create(
            tenant=self.tenant,
            name="Optional device",
            asset_tag="OPTIONAL-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={},
        )
        form = AssetForm(
            request=self.request,
            data={
                "name": asset.name,
                "asset_tag": asset.asset_tag,
                "serial_number": "",
                "asset_type": asset_type.pk,
                "asset_role": "",
                "status": status.pk,
                "location": "",
                "tenant": self.tenant.pk,
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
        clearable = CustomField.objects.create(
            name="clearable_value",
            namespace="local",
            label="Clearable value",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
        )
        clearable.object_types.add(ContentType.objects.get_for_model(Asset))
        status = StatusLabel.objects.create(name="Available clear", slug="available-clear", type="deployable")
        manufacturer = Manufacturer.objects.create(name="Clear Manufacturer", slug="clear-manufacturer")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Clear Device", slug="clear-device")
        asset = Asset.objects.create(
            tenant=self.tenant,
            name="Clear device",
            asset_tag="CLEAR-1",
            asset_type=asset_type,
            status=status,
            custom_field_data={"clearable_value": "remove me"},
        )
        form = AssetForm(
            request=self.request,
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


def _asset_switch_pair():
    """Asset on a plain type plus a target type with a required specification."""
    manufacturer = Manufacturer.objects.create(name="Switch Manufacturer", slug="switch-manufacturer")
    plain_type = AssetType.objects.create(manufacturer=manufacturer, model="Plain Device", slug="plain-device")
    required = CustomField.objects.create(
        name="required_spec",
        namespace="local",
        label="Required specification",
        activation=CustomField.ACTIVATION_COMPOSED,
        required=True,
    )
    required.object_types.add(ContentType.objects.get_for_model(Asset))
    fieldset = CustomFieldset.objects.create(namespace="local", slug="switch-required", label="Switch required")
    CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=required, position=10)
    target_type = AssetType.objects.create(manufacturer=manufacturer, model="Target Device", slug="target-device")
    AssetTypeFieldset.objects.create(asset_type=target_type, fieldset=fieldset, position=10)
    status = StatusLabel.objects.create(name="Available switch", slug="available-switch", type="deployable")
    asset = Asset.objects.create(
        name="Switch me",
        asset_tag="SWITCH-1",
        asset_type=plain_type,
        status=status,
    )
    return asset, plain_type, target_type


class AssetTypeSwitchValidationTests(TestCase):
    def test_field_limited_asset_type_switch_runs_target_composition_validation(self):
        asset, plain_type, target_type = _asset_switch_pair()

        asset.asset_type = target_type
        with self.assertRaises(ValidationError):
            asset.save(update_fields=["asset_type"])

        asset.refresh_from_db()
        self.assertEqual(asset.asset_type_id, plain_type.pk)
        self.assertEqual(asset.custom_field_data, {})

    def test_field_limited_asset_type_switch_via_attname_runs_target_validation(self):
        asset, plain_type, target_type = _asset_switch_pair()

        # Django accepts the ``attname`` spelling in ``update_fields``; the
        # dependency guard must not fail open for it.
        asset.asset_type_id = target_type.pk
        with self.assertRaises(ValidationError):
            asset.save(update_fields=["asset_type_id"])

        asset.refresh_from_db()
        self.assertEqual(asset.asset_type_id, plain_type.pk)
        self.assertEqual(asset.custom_field_data, {})

    def test_field_limited_unrelated_asset_save_keeps_suppression_path(self):
        asset, _, target_type = _asset_switch_pair()
        asset.custom_field_data = {"required_spec": "configured"}
        Asset.objects.filter(pk=asset.pk).update(asset_type=target_type, custom_field_data=asset.custom_field_data)
        asset.refresh_from_db()
        self.assertEqual(asset.asset_type_id, target_type.pk)

        # A stored value can legitimately be absent (direct updates bypass the
        # dynamic validator); an unrelated field-limited save must not
        # re-impose dynamic custom-field validation.
        Asset.objects.filter(pk=asset.pk).update(custom_field_data={})
        asset.refresh_from_db()
        asset.name = "Renamed without dynamic validation"
        asset.save(update_fields=["name"])
        asset.refresh_from_db()
        self.assertEqual(asset.name, "Renamed without dynamic validation")

        # The control: touching custom_field_data itself still validates fully.
        with self.assertRaises(ValidationError):
            asset.save(update_fields=["custom_field_data"])

    def test_field_limited_asset_type_switch_succeeds_when_target_values_present(self):
        asset, _, target_type = _asset_switch_pair()

        asset.custom_field_data = {"required_spec": "configured"}
        asset.save(update_fields=["custom_field_data"])
        asset.asset_type = target_type
        asset.save(update_fields=["asset_type"])

        asset.refresh_from_db()
        self.assertEqual(asset.asset_type_id, target_type.pk)
        self.assertEqual(asset.custom_field_data, {"required_spec": "configured"})


class AssetFormCommandAdapterTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="asset-form-editor", password="password")
        self.tenant = Tenant.objects.create(name="Form tenant", slug="form-tenant")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Asset form editor",
            permissions=["assets.change_asset"],
        )
        self.grant(self.user, self.tenant, role)
        self.manufacturer = Manufacturer.objects.create(name="Form maker", slug="form-maker")
        self.status = StatusLabel.objects.create(name="Form available", slug="form-available", type="deployable")
        AssetTagSequence._base_manager.create(
            tenant=self.tenant,
            prefix="FORM-ASSET-",
            next_value=1,
            zero_padding=6,
            is_active=True,
        )

    def _asset_field(self, name, *, required=False):
        field = CustomField.objects.create(
            name=name,
            namespace="local",
            label=name.replace("_", " ").title(),
            activation=CustomField.ACTIVATION_COMPOSED,
            required=required,
        )
        field.object_types.add(ContentType.objects.get_for_model(Asset))
        return field

    def _fieldset(self, slug, field):
        fieldset = CustomFieldset.objects.create(namespace="local", slug=slug, label=slug.replace("-", " ").title())
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=10)
        return fieldset

    def test_tenant_authorized_form_switch_uses_destination_definition_revision(self):
        source_field = self._asset_field("source_revision_spec")
        target_field = self._asset_field("target_revision_spec", required=True)
        source_fieldset = self._fieldset("source-revision", source_field)
        target_fieldset = self._fieldset("target-revision", target_field)
        source_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Source device",
            slug="source-device",
        )
        target_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Target device",
            slug="target-device",
        )
        AssetTypeFieldset.objects.create(asset_type=source_type, fieldset=source_fieldset, position=10)
        AssetTypeFieldset.objects.create(asset_type=target_type, fieldset=target_fieldset, position=10)
        asset = Asset.objects.create(
            name="Switch through form",
            asset_tag="FORM-SWITCH-1",
            tenant=self.tenant,
            asset_type=source_type,
            status=self.status,
            custom_field_data={"source_revision_spec": "legacy source value"},
        )

        request = RequestFactory().post(f"/assets/{asset.pk}/edit/")
        request.user = self.user
        form = AssetForm(
            data=_minimal_asset_form_data(
                asset,
                self.status,
                target_type,
                tenant=str(self.tenant.pk),
                cf_target_revision_spec="target value",
            ),
            instance=asset,
            request=request,
        )

        self.assertTrue(form.is_valid(), form.errors)
        source_definition, _ = load_effective_definition(source_type.pk, "asset", ("source_revision_spec",))
        target_definition, _ = load_effective_definition(target_type.pk, "asset", ("source_revision_spec",))
        self.assertNotEqual(source_definition.revision, target_definition.revision)
        adapter_plan = current_specification_plan(asset, target_kind="asset", asset_type_id=target_type.pk)
        self.assertEqual(adapter_plan.definition_revision, target_definition.revision)
        saved = form.save()

        saved.refresh_from_db()
        self.assertEqual(saved.asset_type_id, target_type.pk)
        self.assertEqual(
            saved.custom_field_data,
            {
                "source_revision_spec": "legacy source value",
                "target_revision_spec": "target value",
            },
        )
