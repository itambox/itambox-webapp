"""Database-backed HTTP proof for the adopted GraphQL specification surface."""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from assets.api.tests.test_type_library_http import EXPORT_URL, PREVIEW_URL
from assets.models import Asset, AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from assets.tests import test_graphql as graphql_fixtures
from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.models import ObjectChange
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    CustomFieldsetField,
    SpecificationLibrary,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "tests" / "fixtures" / "type_library"


class GraphQLSpecificationHTTPTests(TestCase):
    """Exercise GraphQL adapters through Django's real HTTP/auth/database path."""

    def setUp(self):
        graphql_fixtures.GraphQLTestCase.setUp(self)
        self._grant_specification_permissions()
        self.specification_field = CustomField.objects.create(
            namespace="graphql",
            name="graphql_http_note",
            label="GraphQL HTTP note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        self.specification_field.object_types.add(
            ContentType.objects.get_for_model(AssetType),
            ContentType.objects.get_for_model(Asset),
        )
        self.specification_fieldset = CustomFieldset.objects.create(
            namespace="graphql",
            slug="http",
            label="GraphQL HTTP",
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(
            fieldset=self.specification_fieldset,
            custom_field=self.specification_field,
            position=1,
        )
        self.field_identity = f"{self.specification_field.namespace}/{self.specification_field.name}"
        self.fieldset_identity = f"{self.specification_fieldset.namespace}/{self.specification_fieldset.slug}"

    def tearDown(self):

        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)
        super().tearDown()

    def _grant_specification_permissions(self):
        role_permissions = {
            "assets.view_assettype",
            "assets.add_assettype",
            "assets.change_assettype",
            "assets.view_category",
            "assets.change_category",
            "extras.manage_specification_library",
            "extras.view_specificationlibrary",
            "extras.view_customfield",
            "extras.add_customfield",
            "extras.change_customfield",
            "extras.view_customfieldchoiceset",
            "extras.add_customfieldchoiceset",
            "extras.change_customfieldchoiceset",
            "extras.view_customfieldchoice",
            "extras.add_customfieldchoice",
            "extras.change_customfieldchoice",
            "extras.view_customfieldset",
            "extras.add_customfieldset",
            "extras.change_customfieldset",
        }
        self.role_admin_a.permissions = sorted(set(self.role_admin_a.permissions) | role_permissions)
        self.role_admin_a.save(update_fields=["permissions"])

        for model in (
            AssetType,
            Category,
            CustomField,
            CustomFieldChoiceSet,
            CustomFieldChoice,
            CustomFieldset,
            Manufacturer,
            SpecificationLibrary,
        ):
            content_type = ContentType.objects.get_for_model(model)
            codenames = [f"{action}_{model._meta.model_name}" for action in ("add", "change", "view")]
            self.staff_a.user_permissions.add(
                *Permission.objects.filter(content_type=content_type, codename__in=codenames)
            )

        library_content_type = ContentType.objects.get_for_model(SpecificationLibrary)
        manage_permission = Permission.objects.get(
            content_type=library_content_type,
            codename="manage_specification_library",
        )
        self.staff_a.user_permissions.add(manage_permission)
        # HTTP token authentication establishes the request's tenant context.
        # An ambient has_perm() check here is not that authorization boundary.
        self.assertTrue(self.staff_a.user_permissions.filter(pk=manage_permission.pk).exists())

    def _graphql_response(self, query, variables=None, *, token=None):
        token = token or self.token_a
        body = {"query": query}
        if variables is not None:
            body["variables"] = variables
        response = self.client.post(
            self.graphql_url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )
        return response, response.json()

    def _graphql_data(self, query, variables=None, *, token=None):
        response, payload = self._graphql_response(query, variables, token=token)
        self.assertEqual(response.status_code, 200, payload)
        self.assertNotIn("errors", payload, payload)
        return payload["data"]

    def _assert_no_user_errors(self, payload):
        self.assertEqual(payload["userErrors"], [], payload)

    def _type_plan(self, asset_type_id):
        return self._graphql_data(
            """
            query ($id: ID!) {
              assetType(id: $id) {
                id
                resourceRevision
                specificationDefinition(target: ASSET_TYPE) { revision }
              }
            }
            """,
            {"id": str(asset_type_id)},
        )["assetType"]

    def _asset_plan(self, asset_id, tenant_id):
        return self._graphql_data(
            """
            query ($id: ID!, $scope: RequestedScopeSelector!) {
              asset(id: $id, requestedScope: $scope) {
                id
                resourceRevision
                specificationDefinition { revision }
              }
            }
            """,
            {
                "id": str(asset_id),
                "scope": {"mode": "TENANT", "tenantId": str(tenant_id)},
            },
        )["asset"]

    @staticmethod
    def _text_patch(key, value):
        return {
            "set": [{"key": key, "value": {"text": value}}],
            "clear": [],
        }

    @staticmethod
    def _empty_patch():
        return {"set": [], "clear": []}

    def test_real_http_create_preview_and_category_default_presence(self):
        category = self._graphql_data(
            """
            query ($id: ID!) {
              category(id: $id) { id resourceRevision defaultFieldsets { identity } }
            }
            """,
            {"id": str(self.category.pk)},
        )["category"]

        set_defaults = self._graphql_data(
            """
            mutation ($input: SetCategoryDefaultsInput!) {
              setCategoryDefaults(input: $input) {
                category {
                  id
                  resourceRevision
                  defaultFieldsets { identity }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "categoryId": str(self.category.pk),
                    "expectedResourceRevision": category["resourceRevision"],
                    "fieldsets": [self.fieldset_identity],
                }
            },
        )["setCategoryDefaults"]
        self._assert_no_user_errors(set_defaults)
        self.assertEqual(
            [item["identity"] for item in set_defaults["category"]["defaultFieldsets"]],
            [self.fieldset_identity],
        )
        self.assertEqual(
            list(
                CategoryDefaultFieldset.objects.filter(category_id=self.category.pk).values_list(
                    "fieldset__namespace", "fieldset__slug"
                )
            ),
            [("graphql", "http")],
        )

        omitted_input = {
            "manufacturerId": str(self.manufacturer.pk),
            "model": "GraphQL Omitted Defaults",
            "categoryId": str(self.category.pk),
            "patch": self._text_patch(self.specification_field.name, "created by GraphQL"),
        }
        omitted_preview = self._graphql_data(
            """
            mutation ($input: PreviewAssetTypeCreateInput!) {
              previewAssetTypeCreate(input: $input) {
                preview {
                  previewToken
                  expectedDefinitionRevision
                  expectedResourceRevision
                  categoryDefaultSnapshotRevision
                  consumesCategoryDefaults
                  definition {
                    revision
                    sections { identity fields { key } }
                  }
                }
                userErrors { code path }
              }
            }
            """,
            {"input": omitted_input},
        )["previewAssetTypeCreate"]
        self._assert_no_user_errors(omitted_preview)
        omitted = omitted_preview["preview"]
        self.assertTrue(omitted["consumesCategoryDefaults"])
        self.assertTrue(omitted["previewToken"])
        self.assertTrue(omitted["categoryDefaultSnapshotRevision"])
        self.assertIn(
            self.specification_field.name,
            [field["key"] for section in omitted["definition"]["sections"] for field in section["fields"]],
        )

        explicit_preview = self._graphql_data(
            """
            mutation ($input: PreviewAssetTypeCreateInput!) {
              previewAssetTypeCreate(input: $input) {
                preview {
                  previewToken
                  expectedDefinitionRevision
                  categoryDefaultSnapshotRevision
                  consumesCategoryDefaults
                  definition { sections { identity } }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    **omitted_input,
                    "model": "GraphQL Explicit Empty",
                    "fieldsets": [],
                    "patch": self._empty_patch(),
                }
            },
        )["previewAssetTypeCreate"]
        self._assert_no_user_errors(explicit_preview)
        explicit = explicit_preview["preview"]
        self.assertFalse(explicit["consumesCategoryDefaults"])
        self.assertIsNone(explicit["categoryDefaultSnapshotRevision"])
        self.assertEqual(explicit["definition"]["sections"], [])

        created_data = self._graphql_data(
            """
            mutation ($input: CreateAssetTypeInput!) {
              createAssetType(input: $input) {
                assetType {
                  id
                  model
                  category { id }
                  specificationEntries {
                    key
                    value {
                      __typename
                      ... on TextSpecificationValue { text }
                    }
                  }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    **omitted_input,
                    "expectedDefinitionRevision": omitted["expectedDefinitionRevision"],
                    "previewToken": omitted["previewToken"],
                    "expectedCategoryDefaultSnapshotRevision": omitted["categoryDefaultSnapshotRevision"],
                }
            },
        )["createAssetType"]
        self._assert_no_user_errors(created_data)
        created = AssetType.all_objects.get(model="GraphQL Omitted Defaults")
        self.assertEqual(created_data["assetType"]["id"], str(created.pk))
        self.assertEqual(created.category_id, self.category.pk)
        self.assertEqual(created.custom_field_data, {self.specification_field.name: "created by GraphQL"})
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=created).values_list("fieldset_id", "position")),
            [(self.specification_fieldset.pk, 1)],
        )

        explicit_created = self._graphql_data(
            """
            mutation ($input: CreateAssetTypeInput!) {
              createAssetType(input: $input) {
                assetType { id model }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    **omitted_input,
                    "model": "GraphQL Explicit Empty",
                    "fieldsets": [],
                    "expectedDefinitionRevision": explicit["expectedDefinitionRevision"],
                    "patch": self._empty_patch(),
                }
            },
        )["createAssetType"]
        self._assert_no_user_errors(explicit_created)
        explicit_type = AssetType.all_objects.get(model="GraphQL Explicit Empty")
        self.assertEqual(explicit_created["assetType"]["id"], str(explicit_type.pk))
        self.assertEqual(explicit_type.custom_field_data, {})
        self.assertFalse(AssetTypeFieldset.objects.filter(asset_type=explicit_type).exists())

    def _composition_plan(self, fieldsets):
        # The existing REST preview exposes the prospective composition revision;
        # the current GraphQL definition is not the requested new composition.
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {self.token_a.key}")
        response = api.post(
            f"/api/assets/asset-types/{self.asset_type.pk}/composition-preview/",
            {"fieldsets": fieldsets, "specification_patch": {"set": {}, "clear": []}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["can_apply"], response.data)
        return response.data

    def test_real_http_composition_and_asset_update_persist_typed_values(self):
        initial_plan = self._composition_plan([self.fieldset_identity])
        composed = self._graphql_data(
            """
            mutation ($input: SetAssetTypeCompositionInput!) {
              setAssetTypeComposition(input: $input) {
                assetType {
                  id
                  resourceRevision
                  specificationEntries {
                    key
                    state
                    value {
                      __typename
                      ... on TextSpecificationValue { text }
                    }
                  }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetTypeId": str(self.asset_type.pk),
                    "expectedResourceRevision": initial_plan["expected_resource_revision"],
                    "expectedDefinitionRevision": initial_plan["expected_definition_revision"],
                    "fieldsets": [self.fieldset_identity],
                    "patch": self._text_patch(self.specification_field.name, "type value"),
                }
            },
        )["setAssetTypeComposition"]
        self._assert_no_user_errors(composed)
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.custom_field_data, {self.specification_field.name: "type value"})
        self.assertEqual(
            list(AssetTypeFieldset.objects.filter(asset_type=self.asset_type).values_list("fieldset_id", "position")),
            [(self.specification_fieldset.pk, 1)],
        )
        type_entry = composed["assetType"]["specificationEntries"][0]
        self.assertEqual(type_entry["key"], self.specification_field.name)
        self.assertEqual(type_entry["value"], {"__typename": "TextSpecificationValue", "text": "type value"})

        asset_plan = self._asset_plan(self.asset_a.pk, self.tenant_a.pk)
        updated = self._graphql_data(
            """
            mutation ($input: UpdateAssetSpecificationsInput!) {
              updateAssetSpecifications(input: $input) {
                asset {
                  id
                  resourceRevision
                  specificationEntries {
                    key
                    value {
                      __typename
                      ... on TextSpecificationValue { text }
                    }
                  }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetId": str(self.asset_a.pk),
                    "requestedScope": {"mode": "TENANT", "tenantId": str(self.tenant_a.pk)},
                    "expectedResourceRevision": asset_plan["resourceRevision"],
                    "expectedDefinitionRevision": asset_plan["specificationDefinition"]["revision"],
                    "patch": self._text_patch(self.specification_field.name, "asset value"),
                }
            },
        )["updateAssetSpecifications"]
        self._assert_no_user_errors(updated)
        self.asset_a.refresh_from_db()
        self.assertEqual(self.asset_a.custom_field_data, {self.specification_field.name: "asset value"})
        self.assertEqual(
            updated["asset"]["specificationEntries"][0]["value"],
            {"__typename": "TextSpecificationValue", "text": "asset value"},
        )

        clear_plan = self._composition_plan([])
        cleared = self._graphql_data(
            """
            mutation ($input: SetAssetTypeCompositionInput!) {
              setAssetTypeComposition(input: $input) {
                assetType { id specificationEntries { key state } }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetTypeId": str(self.asset_type.pk),
                    "expectedResourceRevision": clear_plan["expected_resource_revision"],
                    "expectedDefinitionRevision": clear_plan["expected_definition_revision"],
                    "fieldsets": [],
                    "patch": self._empty_patch(),
                }
            },
        )["setAssetTypeComposition"]
        self._assert_no_user_errors(cleared)
        self.asset_type.refresh_from_db()
        self.assertFalse(AssetTypeFieldset.objects.filter(asset_type=self.asset_type).exists())
        self.assertEqual(self.asset_type.custom_field_data, {self.specification_field.name: "type value"})
        self.assertEqual(cleared["assetType"]["specificationEntries"][0]["state"], "HISTORICAL")
        self.asset_a.refresh_from_db()
        self.assertEqual(self.asset_a.custom_field_data, {self.specification_field.name: "asset value"})
        observed_history = self._graphql_data(
            """
            query ($id: ID!, $scope: RequestedScopeSelector!) {
              asset(id: $id, requestedScope: $scope) {
                specificationEntries {
                  key state value { ... on TextSpecificationValue { text } }
                }
              }
            }
            """,
            {"id": str(self.asset_a.pk), "scope": {"mode": "TENANT", "tenantId": str(self.tenant_a.pk)}},
        )["asset"]["specificationEntries"]
        self.assertEqual(
            observed_history,
            [{"key": self.specification_field.name, "state": "HISTORICAL", "value": {"text": "asset value"}}],
        )

    def test_real_http_stale_denied_and_explicit_null_updates_leave_state_and_audit_unchanged(self):
        plan = self._asset_plan(self.asset_a.pk, self.tenant_a.pk)
        before_value = dict(self.asset_a.custom_field_data)
        before_changes = ObjectChange._base_manager.filter(
            changed_object_id=self.asset_a.pk,
            changed_object_type=ContentType.objects.get_for_model(self.asset_a),
        ).count()

        stale = self._graphql_data(
            """
            mutation ($input: UpdateAssetSpecificationsInput!) {
              updateAssetSpecifications(input: $input) {
                asset { id }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetId": str(self.asset_a.pk),
                    "requestedScope": {"mode": "TENANT", "tenantId": str(self.tenant_a.pk)},
                    "expectedResourceRevision": "sha256:stale-resource",
                    "expectedDefinitionRevision": plan["specificationDefinition"]["revision"],
                    "patch": self._empty_patch(),
                }
            },
        )["updateAssetSpecifications"]
        self.assertIsNone(stale["asset"])
        self.assertEqual(stale["userErrors"][0]["code"], "STALE_RESOURCE")

        denied = self._graphql_data(
            """
            mutation ($input: UpdateAssetSpecificationsInput!) {
              updateAssetSpecifications(input: $input) {
                asset { id }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetId": str(self.asset_a.pk),
                    "requestedScope": {"mode": "TENANT", "tenantId": str(self.tenant_b.pk)},
                    "expectedResourceRevision": plan["resourceRevision"],
                    "expectedDefinitionRevision": plan["specificationDefinition"]["revision"],
                    "patch": self._empty_patch(),
                }
            },
        )["updateAssetSpecifications"]
        self.assertIsNone(denied["asset"])
        self.assertEqual(denied["userErrors"][0]["code"], "OBJECT_UNAVAILABLE")

        explicit_null = self._graphql_data(
            """
            mutation ($input: UpdateAssetSpecificationsInput!) {
              updateAssetSpecifications(input: $input) {
                asset { id }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "assetId": str(self.asset_a.pk),
                    "assetTypeId": None,
                    "requestedScope": {"mode": "TENANT", "tenantId": str(self.tenant_a.pk)},
                    "expectedResourceRevision": plan["resourceRevision"],
                    "expectedDefinitionRevision": plan["specificationDefinition"]["revision"],
                    "patch": self._empty_patch(),
                }
            },
        )["updateAssetSpecifications"]
        self.assertIsNone(explicit_null["asset"])
        self.assertEqual(explicit_null["userErrors"][0]["code"], "INVALID_TYPE")
        self.assertEqual(explicit_null["userErrors"][0]["path"], ["input", "assetTypeId"])

        self.asset_a.refresh_from_db()
        self.assertEqual(self.asset_a.custom_field_data, before_value)
        self.assertEqual(
            ObjectChange._base_manager.filter(
                changed_object_id=self.asset_a.pk,
                changed_object_type=ContentType.objects.get_for_model(self.asset_a),
            ).count(),
            before_changes,
        )

    def test_real_http_definition_and_choice_commands_persist_and_reject_stale_add(self):
        created_field = self._graphql_data(
            """
            mutation ($input: CreateSpecificationFieldInput!) {
              createSpecificationField(input: $input) {
                field { identity key label resourceRevision }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "key": "rack_owner",
                    "namespace": "http",
                    "label": "Rack owner",
                    "targets": ["ASSET_TYPE", "ASSET"],
                    "activation": "COMPOSED",
                    "fieldType": "TEXT",
                    "required": False,
                    "nullable": False,
                    "validation": {},
                }
            },
        )["createSpecificationField"]
        self._assert_no_user_errors(created_field)
        field = created_field["field"]
        self.assertTrue(CustomField.objects.filter(namespace="http", name="http__rack_owner").exists())
        self.assertEqual(field["identity"], "http/http__rack_owner")

        created_fieldset = self._graphql_data(
            """
            mutation ($input: CreateSpecificationFieldsetInput!) {
              createSpecificationFieldset(input: $input) {
                fieldset {
                  identity
                  resourceRevision
                  fields { identity key }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "identity": "http/hardware",
                    "label": "Hardware",
                    "description": "",
                    "fields": [field["identity"]],
                }
            },
        )["createSpecificationFieldset"]
        self._assert_no_user_errors(created_fieldset)
        self.assertEqual(
            [item["identity"] for item in created_fieldset["fieldset"]["fields"]],
            [field["identity"]],
        )
        self.assertTrue(CustomFieldset.objects.filter(namespace="http", slug="hardware").exists())

        created_choices = self._graphql_data(
            """
            mutation ($input: CreateChoiceSetInput!) {
              createChoiceSet(input: $input) {
                choiceSet {
                  identity
                  resourceRevision
                  choices { key label lifecycle }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "identity": "http/ownership",
                    "label": "Ownership",
                    "choices": [{"key": "operator", "label": "Operator"}],
                }
            },
        )["createChoiceSet"]
        self._assert_no_user_errors(created_choices)
        choice_set = created_choices["choiceSet"]
        self.assertEqual(choice_set["identity"], "http/ownership")
        self.assertEqual([choice["key"] for choice in choice_set["choices"]], ["operator"])

        added = self._graphql_data(
            """
            mutation ($input: AddChoiceInput!) {
              addChoice(input: $input) {
                choiceSet {
                  identity
                  resourceRevision
                  choices { key label lifecycle }
                }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "choiceSet": choice_set["identity"],
                    "expectedResourceRevision": choice_set["resourceRevision"],
                    "choice": {"key": "supplier", "label": "Supplier"},
                }
            },
        )["addChoice"]
        self._assert_no_user_errors(added)
        self.assertEqual(
            [choice["key"] for choice in added["choiceSet"]["choices"]],
            ["operator", "supplier"],
        )

        stale_add = self._graphql_data(
            """
            mutation ($input: AddChoiceInput!) {
              addChoice(input: $input) {
                choiceSet { identity }
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "choiceSet": choice_set["identity"],
                    "expectedResourceRevision": choice_set["resourceRevision"],
                    "choice": {"key": "auditor", "label": "Auditor"},
                }
            },
        )["addChoice"]
        self.assertIsNone(stale_add["choiceSet"])
        self.assertEqual(stale_add["userErrors"][0]["code"], "STALE_RESOURCE")
        self.assertEqual(
            CustomFieldChoice.objects.filter(choice_set__namespace="http", choice_set__slug="ownership").count(),
            2,
        )

    def test_real_http_library_preview_apply_export_matches_rest_and_errors(self):
        release_text = (FIXTURE_ROOT / "example-laptop-library-v1.json").read_text(encoding="utf-8")
        rest_client = APIClient()
        rest_client.credentials(HTTP_AUTHORIZATION=f"Token {self.token_a.key}")
        rest_preview = rest_client.post(PREVIEW_URL, {"document": release_text}, format="json")
        self.assertEqual(rest_preview.status_code, 200, rest_preview.data)

        graphql_preview = self._graphql_data(
            """
            mutation ($input: LibraryPreviewInput!) {
              previewLibrary(input: $input) {
                token
                digest
                canApply
                actions { id kind identity path message blocking }
                userErrors { code path }
              }
            }
            """,
            {"input": {"documentText": release_text, "resolutions": []}},
        )["previewLibrary"]
        self.assertEqual(graphql_preview["userErrors"], [])
        self.assertEqual(graphql_preview["digest"], rest_preview.data["plan"]["plan_digest"])
        self.assertEqual(graphql_preview["canApply"], rest_preview.data["can_apply"])
        self.assertEqual(len(graphql_preview["actions"]), len(rest_preview.data["plan"]["actions"]))
        for graphql_action, rest_action in zip(
            graphql_preview["actions"], rest_preview.data["plan"]["actions"], strict=True
        ):
            self.assertEqual(graphql_action["id"], rest_action["action_id"])
            self.assertEqual(graphql_action["kind"], rest_action["action"])
            self.assertEqual(graphql_action["identity"], rest_action["identity"])
            self.assertEqual(graphql_action["path"], rest_action["path"])
            self.assertEqual(graphql_action["message"], rest_action["reason"])
            self.assertEqual(
                graphql_action["blocking"],
                rest_action["action"] == "conflict" or rest_action["decision"] in {"abort", "conflict"},
            )

        applied = self._graphql_data(
            """
            mutation ($input: LibraryApplyInput!) {
              applyLibrary(input: $input) {
                applied
                changed
                acceptedRelease
                userErrors { code path }
              }
            }
            """,
            {
                "input": {
                    "documentText": release_text,
                    "planToken": graphql_preview["token"],
                    "resolutions": [],
                }
            },
        )["applyLibrary"]
        self.assertEqual(applied["userErrors"], [])
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["changed"])
        self.assertEqual(applied["acceptedRelease"], 1)
        self.assertTrue(SpecificationLibrary.objects.filter(namespace="example").exists())

        rest_original = rest_client.post(
            EXPORT_URL,
            {"namespace": "example", "mode": "original_release"},
            format="json",
        )
        self.assertEqual(rest_original.status_code, 200, rest_original.data)
        graphql_original = self._graphql_data(
            """
            mutation ($namespace: String!) {
              exportLibrary(namespace: $namespace, mode: ORIGINAL_RELEASE) {
                documentText
                semanticDigest
                userErrors { code path }
              }
            }
            """,
            {"namespace": "example"},
        )["exportLibrary"]
        self.assertEqual(graphql_original["userErrors"], [])
        self.assertEqual(
            json.loads(graphql_original["documentText"]),
            rest_original.data["document"],
        )
        self.assertEqual(graphql_original["semanticDigest"], rest_original.data["semantic_digest"])

        source_choice_set = json.loads(release_text)["definitions"]["choice_sets"][0]
        retained_set = CustomFieldChoiceSet.objects.get(
            namespace="example", slug=source_choice_set["id"].split("/", 1)[1]
        )
        retained_choice = CustomFieldChoice.objects.create(
            choice_set=retained_set,
            key="retired-old-choice",
            label="Retained old choice",
            position=1000,
            lifecycle="deprecated",
            deprecated_at=timezone.now(),
        )
        audit_before_export = ObjectChange._base_manager.count()
        choice_before_export = CustomFieldChoice._base_manager.filter(pk=retained_choice.pk).values().get()

        rest_effective = rest_client.post(
            EXPORT_URL,
            {
                "namespace": "example",
                "mode": "effective_snapshot",
                "acknowledge_retained_history": True,
            },
            format="json",
        )
        self.assertEqual(rest_effective.status_code, 200, rest_effective.data)
        graphql_effective = self._graphql_data(
            """
            mutation ($namespace: String!) {
              exportLibrary(namespace: $namespace, mode: EFFECTIVE_SNAPSHOT) {
                documentText
                semanticDigest
                userErrors { code path }
              }
            }
            """,
            {"namespace": "example"},
        )["exportLibrary"]
        self.assertEqual(graphql_effective["userErrors"], [])
        self.assertEqual(
            json.loads(graphql_effective["documentText"]),
            rest_effective.data["document"],
        )
        self.assertEqual(graphql_effective["semanticDigest"], rest_effective.data["semantic_digest"])
        self.assertEqual(
            CustomFieldChoice._base_manager.filter(pk=retained_choice.pk).values().get(), choice_before_export
        )
        self.assertEqual(ObjectChange._base_manager.count(), audit_before_export)
        retained_set_document = next(
            item
            for item in rest_effective.data["document"]["effective_definitions"]["choice_sets"]
            if item["id"] == source_choice_set["id"]
        )
        self.assertIn(
            {"key": "retired-old-choice", "label": "Retained old choice", "lifecycle": "deprecated"},
            retained_set_document["choices"],
        )

        rest_missing = rest_client.post(
            EXPORT_URL,
            {"namespace": "does-not-exist", "mode": "original_release"},
            format="json",
        )
        self.assertEqual(rest_missing.status_code, 404, rest_missing.data)
        graphql_missing = self._graphql_data(
            """
            mutation ($namespace: String!) {
              exportLibrary(namespace: $namespace, mode: ORIGINAL_RELEASE) {
                documentText
                semanticDigest
                userErrors { code path }
              }
            }
            """,
            {"namespace": "does-not-exist"},
        )["exportLibrary"]
        self.assertIsNone(graphql_missing["documentText"])
        self.assertEqual(
            graphql_missing["userErrors"][0]["code"],
            rest_missing.data["error"]["code"],
        )

        read_only_token = type(self.token_a).objects.create(
            user=self.staff_a,
            tenant=self.tenant_a,
            write_enabled=False,
        )
        read_only = self._graphql_response(
            """
            mutation ($input: LibraryPreviewInput!) {
              previewLibrary(input: $input) { token }
            }
            """,
            {"input": {"documentText": release_text, "resolutions": []}},
            token=read_only_token,
        )
        self.assertEqual(read_only[0].status_code, 401)
        rest_read_only = APIClient()
        rest_read_only.credentials(HTTP_AUTHORIZATION=f"Token {read_only_token.key}")
        rest_read_only_response = rest_read_only.post(PREVIEW_URL, {"document": release_text}, format="json")
        self.assertEqual(rest_read_only_response.status_code, 401)
