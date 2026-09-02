from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.models import Asset, AssetType
from extras.api.serializers import CustomFieldSerializer
from extras.models import CustomFieldChoiceSet


class CustomFieldAPISerializerContractTests(TestCase):
    def setUp(self):
        self.asset_ct = ContentType.objects.get_for_model(Asset)
        self.asset_type_ct = ContentType.objects.get_for_model(AssetType)
        self.choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="api-stage-choices",
            label="API Stage Choices",
        )

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
