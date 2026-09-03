import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from assets.api.serializers import AssetSerializer, AssetTypeSerializer
from assets.models import Asset, AssetType, AssetTypeFieldset, Manufacturer
from extras.api.serializers import CustomFieldSerializer, CustomFieldsetSerializer
from extras.models import CustomField, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from itambox.api.viewsets import ITAMBoxModelViewSet


class CustomFieldAPISerializerContractTests(TestCase):
    def setUp(self):
        self.asset_ct = ContentType.objects.get_for_model(Asset)
        self.asset_type_ct = ContentType.objects.get_for_model(AssetType)
        self.choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="api-stage-choices",
            label="API Stage Choices",
        )

    def test_definition_api_rejects_invalid_regex(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_stage_invalid_regex",
                "label": "API Stage Invalid Regex",
                "field_type": "text",
                "scope": "asset",
                "regex": "[",
                "object_types": ["asset"],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("regex", serializer.errors)

    def test_definition_api_rejects_unknown_validation_rule(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_unknown_validation_rule",
                "label": "API Unknown Validation Rule",
                "field_type": "text",
                "scope": "asset",
                "validation_rule": "unknown-rule",
                "object_types": ["asset"],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("validation_rule", serializer.errors)

    def test_definition_api_rejects_empty_applicability(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_empty_applicability",
                "label": "API Empty Applicability",
                "field_type": "text",
                "scope": None,
                "object_types": [],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("object_types", serializer.errors)

    def test_fieldset_api_rejects_immutable_identity_change(self):
        fieldset = CustomFieldset.objects.create(namespace="local", slug="immutable-api", label="Immutable API")
        serializer = CustomFieldsetSerializer(
            instance=fieldset,
            data={"namespace": "other"},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("namespace", serializer.errors)

    def test_select_definition_requires_choice_set_and_applicable_object_type(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_stage_select",
                "label": "API Stage Select",
                "field_type": "single-select",
                "scope": "asset",
                "max_values": 1,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("choice_set", serializer.errors)
        self.assertIn("object_types", serializer.errors)

    def test_scoped_definition_rejects_mismatched_object_types(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_stage_scoped",
                "label": "API Stage Scoped",
                "field_type": "text",
                "scope": "asset",
                "object_types": ["assettype"],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("object_types", serializer.errors)

    def test_valid_select_definition_accepts_matching_contract(self):
        serializer = CustomFieldSerializer(
            data={
                "name": "api_stage_valid_select",
                "label": "API Stage Valid Select",
                "field_type": "single-select",
                "scope": "asset",
                "max_values": 1,
                "choice_set": self.choice_set.pk,
                "object_types": ["asset"],
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        field = serializer.save()
        self.assertEqual(field.choice_set_id, self.choice_set.pk)
        self.assertEqual(set(field.object_types.values_list("pk", flat=True)), {self.asset_ct.pk})
        self.assertNotIn(self.asset_type_ct.pk, field.object_types.values_list("pk", flat=True))

    def test_api_update_rejects_core_definition(self):
        field = CustomField.objects.create(
            name="api_managed_update",
            label="API Managed Update",
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        view = ITAMBoxModelViewSet()
        view.serializer_class = CustomFieldSerializer
        view.queryset = CustomField.objects.all()
        view.request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=True),
            META={"HTTP_IF_MATCH": f"W/{json.dumps(field.updated_at.isoformat())}"},
            query_params={},
        )
        serializer = SimpleNamespace(instance=field, validated_data={})
        serializer.save = lambda **kwargs: field

        original_guard = view._ensure_unmanaged_definition

        def transition_after_initial_check(instance):
            CustomField.all_objects.filter(pk=instance.pk).update(management_kind=CustomField.MANAGEMENT_CORE)
            return original_guard(instance)

        with patch.object(view, "_ensure_unmanaged_definition", side_effect=transition_after_initial_check) as guard:
            with self.assertRaises(PermissionDenied):
                view.perform_update(serializer)

        self.assertEqual(guard.call_count, 2)
        field.refresh_from_db()
        self.assertEqual(field.management_kind, CustomField.MANAGEMENT_CORE)

    def test_api_delete_rejects_library_definition(self):
        field = CustomField.objects.create(
            name="api_managed_delete",
            label="API Managed Delete",
            management_kind=CustomField.MANAGEMENT_LIBRARY,
        )
        view = ITAMBoxModelViewSet()
        view.serializer_class = CustomFieldSerializer
        view.queryset = CustomField.objects.all()
        view.request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=True),
            META={"HTTP_IF_MATCH": f"W/{json.dumps(field.updated_at.isoformat())}"},
            query_params={},
        )

        with self.assertRaises(PermissionDenied):
            view.perform_destroy(field)

        self.assertIsNone(field.deleted_at)

    def test_api_delete_rechecks_locked_managed_definition(self):
        field = CustomField.objects.create(
            name="api_locked_delete",
            label="API Locked Delete",
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        view = ITAMBoxModelViewSet()
        view.serializer_class = CustomFieldSerializer
        view.queryset = CustomField.objects.all()
        view.request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=True),
            META={"HTTP_IF_MATCH": f"W/{json.dumps(field.updated_at.isoformat())}"},
            query_params={},
        )
        original_guard = view._ensure_unmanaged_definition

        def transition_after_initial_check(instance):
            CustomField.all_objects.filter(pk=instance.pk).update(management_kind=CustomField.MANAGEMENT_LIBRARY)
            return original_guard(instance)

        with patch.object(view, "_ensure_unmanaged_definition", side_effect=transition_after_initial_check) as guard:
            with self.assertRaises(PermissionDenied):
                view.perform_destroy(field)

        self.assertEqual(guard.call_count, 2)
        field.refresh_from_db()
        self.assertIsNone(field.deleted_at)
        self.assertEqual(field.management_kind, CustomField.MANAGEMENT_LIBRARY)

    def test_asset_type_api_rejects_noncanonical_custom_field_value(self):
        definition = CustomField.objects.create(
            name="api_integer_value",
            label="API Integer Value",
            field_type=CustomField.FIELD_TYPE_INTEGER,
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        definition.object_types.add(self.asset_type_ct)
        manufacturer = Manufacturer.objects.create(name="API Manufacturer")

        serializer = AssetTypeSerializer(
            data={
                "model": "API Test Type",
                "slug": "api-test-type",
                "manufacturer_id": manufacturer.pk,
                "custom_field_data": {"api_integer_value": "1.0"},
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("custom_field_data", serializer.errors)
        self.assertNotIn("Unknown custom field key.", str(serializer.errors))

    def test_asset_type_api_patch_preserves_values_and_enforces_composition(self):
        visible = CustomField.objects.create(
            name="visible_spec",
            label="Visible specification",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        outside = CustomField.objects.create(
            name="outside_spec",
            label="Outside specification",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        visible.object_types.add(self.asset_type_ct)
        outside.object_types.add(self.asset_type_ct)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="api-composed", label="API composed")
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=visible, position=10)
        manufacturer = Manufacturer.objects.create(name="Composition API Manufacturer")
        asset_type = AssetType.objects.create(
            model="Composition API Type",
            slug="composition-api-type",
            manufacturer=manufacturer,
            custom_field_data={"visible_spec": "old", "legacy_spec": "preserve"},
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)

        serializer = AssetTypeSerializer(
            instance=asset_type,
            data={"custom_field_data": {"visible_spec": "new"}},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        asset_type.refresh_from_db()
        self.assertEqual(
            asset_type.custom_field_data,
            {"visible_spec": "new", "legacy_spec": "preserve"},
        )

        clear_serializer = AssetTypeSerializer(
            instance=asset_type,
            data={"custom_field_data": {"clear": ["visible_spec"]}},
            partial=True,
        )
        self.assertTrue(clear_serializer.is_valid(), clear_serializer.errors)
        clear_serializer.save()
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.custom_field_data, {"legacy_spec": "preserve"})

        invalid = AssetTypeSerializer(
            instance=asset_type,
            data={"custom_field_data": {"outside_spec": "must reject"}},
            partial=True,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("custom_field_data", invalid.errors)

    def test_asset_type_api_create_accepts_applicable_custom_field_data(self):
        field = CustomField.objects.create(
            name="create_spec",
            label="Create specification",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        field.object_types.add(self.asset_type_ct)
        manufacturer = Manufacturer.objects.create(name="Create API Maker")
        serializer = AssetTypeSerializer(
            data={
                "model": "Create API Model",
                "slug": "create-api-model",
                "manufacturer_id": manufacturer.pk,
                "custom_field_data": {"create_spec": "created"},
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_asset_type_api_create_rejects_fieldset_only_custom_field_data(self):
        field = CustomField.objects.create(
            name="fieldset_only_create_spec",
            label="Fieldset-only create specification",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        field.object_types.add(self.asset_type_ct)
        fieldset = CustomFieldset.objects.create(namespace="local", slug="create-only-set", label="Create only set")
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=10)
        manufacturer = Manufacturer.objects.create(name="Fieldset Create API Maker")
        serializer = AssetTypeSerializer(
            data={
                "model": "Fieldset Create API Model",
                "slug": "fieldset-create-api-model",
                "manufacturer_id": manufacturer.pk,
                "custom_field_data": {"fieldset_only_create_spec": "must reject"},
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("custom_field_data", serializer.errors)

    def test_asset_api_type_switch_uses_new_composition_for_custom_field_patch(self):
        old_field = CustomField.objects.create(
            name="old_asset_spec",
            label="Old asset specification",
            scope=CustomField.SCOPE_ASSET,
        )
        new_field = CustomField.objects.create(
            name="new_asset_spec",
            label="New asset specification",
            scope=CustomField.SCOPE_ASSET,
        )
        old_field.object_types.add(self.asset_ct)
        new_field.object_types.add(self.asset_ct)
        old_set = CustomFieldset.objects.create(namespace="local", slug="old-asset-set", label="Old asset set")
        new_set = CustomFieldset.objects.create(namespace="local", slug="new-asset-set", label="New asset set")
        CustomFieldsetField.objects.create(fieldset=old_set, custom_field=old_field, position=10)
        CustomFieldsetField.objects.create(fieldset=new_set, custom_field=new_field, position=10)
        manufacturer = Manufacturer.objects.create(name="Switch API Maker")
        old_type = AssetType.objects.create(manufacturer=manufacturer, model="Old type", slug="old-type")
        new_type = AssetType.objects.create(manufacturer=manufacturer, model="New type", slug="new-type")
        AssetTypeFieldset.objects.create(asset_type=old_type, fieldset=old_set, position=10)
        AssetTypeFieldset.objects.create(asset_type=new_type, fieldset=new_set, position=10)
        asset = Asset.objects.create(name="Switchable asset", asset_type=old_type, custom_field_data={})

        serializer = AssetSerializer(
            instance=asset,
            data={"asset_type_id": new_type.pk, "custom_field_data": {"new_asset_spec": "new"}},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_custom_field_bulk_delete_rejects_managed_definition_selection(self):
        user = get_user_model().objects.create_superuser(
            username="bulk-delete-contract-admin",
            email="bulk-delete-contract-admin@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        field = CustomField.objects.create(
            name="bulk_delete_contract_field",
            label="Bulk Delete Contract Field",
            management_kind=CustomField.MANAGEMENT_CORE,
        )

        response = self.client.post(
            reverse("extras:customfield_bulk_delete"),
            data={
                "pk": [str(field.pk)],
                "_confirm": "1",
                "return_url": reverse("extras:customfield_list"),
            },
        )

        self.assertEqual(response.status_code, 403)
        field.refresh_from_db()
        self.assertIsNone(field.deleted_at)

    def test_custom_field_bulk_delete_locks_confirmed_selection(self):
        user = get_user_model().objects.create_superuser(
            username="bulk-lock-contract-admin",
            email="bulk-lock-contract-admin@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        field = CustomField.objects.create(
            name="bulk_lock_contract_field",
            label="Bulk Lock Contract Field",
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        statements = []

        def capture(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture):
            response = self.client.post(
                reverse("extras:customfield_bulk_delete"),
                data={
                    "pk": [str(field.pk)],
                    "_confirm": "1",
                    "return_url": reverse("extras:customfield_list"),
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("FOR UPDATE" in sql.upper() for sql in statements))
        field.refresh_from_db()
        self.assertIsNotNone(field.deleted_at)

    def test_custom_field_bulk_edit_rejects_managed_definition_fields(self):
        user = get_user_model().objects.create_superuser(
            username="bulk-contract-admin",
            email="bulk-contract-admin@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        field = CustomField.objects.create(
            name="bulk_contract_field",
            label="Bulk Contract Field",
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )

        response = self.client.post(
            reverse("extras:customfield_bulk_edit"),
            data={
                "pk": [str(field.pk)],
                "_apply": "1",
                "_selected_fields": ["management_kind"],
                "management_kind": CustomField.MANAGEMENT_CORE,
                "return_url": reverse("extras:customfield_list"),
            },
        )

        self.assertEqual(response.status_code, 403)
        field.refresh_from_db()
        self.assertEqual(field.management_kind, CustomField.MANAGEMENT_LOCAL)
