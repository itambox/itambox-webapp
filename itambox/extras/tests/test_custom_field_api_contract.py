import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from assets.api.serializers import AssetTypeSerializer
from assets.models import Asset, AssetType, Manufacturer
from extras.api.serializers import CustomFieldSerializer
from extras.models import CustomField, CustomFieldChoiceSet
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
                "custom_field_data": {"api_integer_value": "not-an-integer"},
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("custom_field_data", serializer.errors)
