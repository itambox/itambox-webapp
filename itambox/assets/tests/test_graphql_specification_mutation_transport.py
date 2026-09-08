import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assets.api.specification_api import command_result_response, issue_payload
from assets.graphql_specifications import mutations
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    DomainIssueDTO,
    OwnerChangedDTO,
    OwnerRefDTO,
)
from organization.services.access_scope import AccessScopeDeniedDTO, ActorContextDTO, ActorId


class GraphQLSpecificationMutationTransportTests(unittest.TestCase):
    def test_typed_value_arms_preserve_false_zero_and_empty_list(self):
        parsed = mutations._patch(
            {
                "set": [
                    {"key": "enabled", "value": {"boolean": False}},
                    {"key": "limit", "value": {"integer": 0}},
                    {"key": "labels", "value": {"multi_choice": []}},
                    {"key": "cleared", "value": {"null_value": True}},
                ],
                "clear": [],
            }
        )

        self.assertEqual(
            dict(parsed.set_values),
            {"enabled": False, "limit": 0, "labels": (), "cleared": None},
        )

    def test_typed_value_requires_exactly_one_non_null_arm(self):
        with self.assertRaises(mutations._InputError) as context:
            mutations._patch(
                {
                    "set": [
                        {"key": "invalid", "value": {"text": "", "boolean": False}},
                        {"key": "also_invalid", "value": {"null_value": False}},
                    ],
                    "clear": [],
                }
            )

        self.assertEqual([issue.code for issue in context.exception.issues], ["INVALID_TYPE", "INVALID_TYPE"])

    def test_duplicate_setters_and_set_clear_overlap_are_rejected_together(self):
        with self.assertRaises(mutations._InputError) as context:
            mutations._patch(
                {
                    "set": [
                        {"key": "hostname", "value": {"text": "one"}},
                        {"key": "hostname", "value": {"text": "two"}},
                    ],
                    "clear": ["hostname", "hostname"],
                }
            )

        self.assertEqual(
            [issue.code for issue in context.exception.issues],
            ["DUPLICATE_FIELD", "DUPLICATE_FIELD", "CONFLICT_CLEAR_OVERLAP"],
        )

    def test_scope_selector_is_explicit_and_mode_specific(self):
        tenant = mutations._scope_selector({"mode": "tenant", "tenant_id": "7"}, required=True)
        group = mutations._scope_selector({"mode": "tenant_group", "tenant_group_id": "8"}, required=True)
        all_accessible = mutations._scope_selector({"mode": "all_accessible"}, required=True)

        self.assertEqual((tenant.mode, tenant.tenant_id, tenant.tenant_group_id), ("tenant", 7, None))
        self.assertEqual((group.mode, group.tenant_id, group.tenant_group_id), ("tenant_group", None, 8))
        self.assertEqual(
            (all_accessible.mode, all_accessible.tenant_id, all_accessible.tenant_group_id),
            ("all_accessible", None, None),
        )

        with self.assertRaises(mutations._InputError):
            mutations._scope_selector({"mode": "tenant", "tenant_id": "7", "tenant_group_id": "8"}, required=True)
        with self.assertRaises(mutations._InputError):
            mutations._scope_selector(mutations._MISSING, required=True)

    def test_missing_asset_scope_is_payload_error_without_command(self):
        command = Mock()
        input_value = {
            "asset_id": "11",
            "expected_resource_revision": "resource-1",
            "expected_definition_revision": "definition-1",
            "patch": {"set": [], "clear": []},
        }

        with patch.object(mutations, "update_asset_specifications", command):
            payload = mutations.UpdateAssetSpecifications.mutate(None, object(), input_value)

        command.assert_not_called()
        self.assertIsNone(payload.asset)
        self.assertEqual([error.code for error in payload.user_errors], ["MISSING_PRECONDITION"])
        self.assertEqual(tuple(payload.user_errors[0].path), ("input", "requestedScope"))

    def test_scope_authority_denial_is_nondisclosing(self):
        denied = AccessScopeDeniedDTO(outcome="denied", public_code="OBJECT_UNAVAILABLE", public_path=())
        actor = ActorContextDTO(actor_id=ActorId(1), authentication_revision="auth-1")
        info = object()
        requested_scope = {"mode": "tenant", "tenant_id": "7"}

        with (
            patch.object(mutations, "_actor", return_value=actor),
            patch.object(mutations, "resolve_access_scope", return_value=denied) as resolve_scope,
        ):
            authorization = mutations._asset_authorization(info, requested_scope)

        self.assertIsNone(authorization)
        request = resolve_scope.call_args.args[0]
        self.assertEqual(request.operation, "update_asset_specifications")
        self.assertEqual(request.required_permission, "assets.change_asset")
        self.assertEqual(request.selector.mode, "tenant")
        self.assertEqual(request.selector.tenant_id, 7)

    def test_type_history_rejects_supplied_scope_at_adapter_boundary(self):
        with self.assertRaises(mutations.GraphQLError) as context:
            mutations.PreviewSpecificationHistoryCleanup.mutate(
                None,
                object(),
                "11",
                "asset_type",
                [],
                requested_scope={"mode": "tenant", "tenant_id": "7"},
            )

        self.assertEqual(context.exception.extensions["code"], "INVALID_TYPE")
        self.assertEqual(context.exception.extensions["path"], ["requestedScope"])

    def test_default_consuming_create_returns_ordered_missing_preconditions_without_command(self):
        command = Mock()
        input_value = {
            "manufacturer_id": "7",
            "model": "Laptop",
            "category_id": "3",
            "patch": {"set": [], "clear": []},
            "expected_definition_revision": "definition-1",
        }

        with patch.object(mutations, "create_asset_type", command):
            payload = mutations.CreateAssetType.mutate(None, object(), input_value)

        command.assert_not_called()
        self.assertIsNone(payload.asset_type)
        self.assertEqual(
            [error.code for error in payload.user_errors],
            ["MISSING_PRECONDITION", "MISSING_PRECONDITION"],
        )
        self.assertEqual(
            [tuple(error.path) for error in payload.user_errors],
            [
                ("input", "previewToken"),
                ("input", "expectedCategoryDefaultSnapshotRevision"),
            ],
        )

    def test_explicit_empty_fieldsets_do_not_consume_category_defaults(self):
        command_result = CommandRejectedDTO(outcome="rejected", safe_owner=None, issues=())
        command = Mock(return_value=command_result)
        actor = object()
        input_value = {
            "manufacturer_id": "7",
            "model": "Laptop",
            "category_id": "3",
            "fieldsets": [],
            "patch": {"set": [], "clear": []},
            "expected_definition_revision": "definition-1",
        }

        with (
            patch.object(mutations, "create_asset_type", command),
            patch.object(mutations, "_actor", return_value=actor),
        ):
            payload = mutations.CreateAssetType.mutate(None, object(), input_value)

        self.assertEqual(payload.user_errors, ())
        kwargs = command.call_args.kwargs
        self.assertEqual(kwargs["actor"], actor)
        self.assertEqual(kwargs["fieldsets"].presence, "explicit")
        self.assertEqual(kwargs["fieldsets"].identities, ())
        self.assertIsNone(kwargs["preview_token"])
        self.assertIsNone(kwargs["expected_category_default_snapshot_revision"])

    def test_asset_update_dispatches_explicit_scope_revisions_and_keep_current_type(self):
        command_result = CommandRejectedDTO(
            outcome="rejected",
            safe_owner=None,
            issues=(
                DomainIssueDTO(
                    code="STALE_RESOURCE",
                    path=(),
                    field_key=None,
                    message_key="specifications.stale_resource",
                ),
            ),
        )
        command = Mock(return_value=command_result)
        authorization = object()
        input_value = {
            "asset_id": "11",
            "requested_scope": {"mode": "tenant", "tenant_id": "7"},
            "expected_resource_revision": "resource-1",
            "expected_definition_revision": "definition-1",
            "patch": {"set": [{"key": "enabled", "value": {"boolean": False}}], "clear": []},
        }
        info = object()

        with (
            patch.object(mutations, "_asset_authorization", return_value=authorization) as authorize,
            patch.object(mutations, "update_asset_specifications", command),
        ):
            payload = mutations.UpdateAssetSpecifications.mutate(None, info, input_value)

        authorize.assert_called_once_with(info, input_value["requested_scope"])
        kwargs = command.call_args.kwargs
        self.assertEqual(kwargs["authorization"], authorization)
        self.assertEqual(kwargs["asset_id"], 11)
        self.assertEqual(kwargs["destination"].presence, "keep_current")
        self.assertIsNone(kwargs["destination"].asset_type_id)
        self.assertEqual(kwargs["expected_resource_revision"], "resource-1")
        self.assertEqual(kwargs["expected_definition_revision"], "definition-1")
        self.assertEqual(dict(kwargs["patch"].set_values), {"enabled": False})
        self.assertEqual(payload.user_errors[0].code, "STALE_RESOURCE")
        self.assertEqual(tuple(payload.user_errors[0].path), ("input", "expectedResourceRevision"))

    def test_stale_scope_plan_is_payload_error(self):
        command_result = CommandRejectedDTO(
            outcome="rejected",
            safe_owner=None,
            issues=(
                DomainIssueDTO(
                    code="STALE_PLAN",
                    path=(),
                    field_key=None,
                    message_key="specifications.stale_plan",
                ),
            ),
        )
        command = Mock(return_value=command_result)
        input_value = {
            "asset_id": "11",
            "requested_scope": {"mode": "tenant", "tenant_id": "7"},
            "expected_resource_revision": "resource-1",
            "expected_definition_revision": "definition-1",
            "patch": {"set": [], "clear": []},
        }

        with (
            patch.object(mutations, "_asset_authorization", return_value=object()),
            patch.object(mutations, "update_asset_specifications", command),
        ):
            payload = mutations.UpdateAssetSpecifications.mutate(None, object(), input_value)

        self.assertEqual([error.code for error in payload.user_errors], ["STALE_PLAN"])
        self.assertEqual(tuple(payload.user_errors[0].path), ("input", "previewToken"))

    def test_revoked_or_missing_scope_returns_nondisclosing_error_without_command(self):
        command = Mock()
        input_value = {
            "asset_id": "11",
            "requested_scope": {"mode": "tenant", "tenant_id": "7"},
            "expected_resource_revision": "resource-1",
            "expected_definition_revision": "definition-1",
            "patch": {"set": [], "clear": []},
        }

        with (
            patch.object(mutations, "_asset_authorization", return_value=None),
            patch.object(mutations, "update_asset_specifications", command),
        ):
            payload = mutations.UpdateAssetSpecifications.mutate(None, object(), input_value)

        command.assert_not_called()
        self.assertIsNone(payload.asset)
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])

    def test_rest_and_graphql_issue_projection_preserve_domain_outcome(self):
        issue = DomainIssueDTO(
            code="STALE_DEFINITION",
            path=(),
            field_key=None,
            message_key="specifications.stale_definition",
        )
        graphql_error = mutations._user_errors((issue,))[0]
        rest_error = issue_payload(issue)

        self.assertEqual(graphql_error.code, rest_error["code"])
        self.assertEqual(graphql_error.field_key, rest_error["field_key"])
        self.assertEqual(graphql_error.message, rest_error["message"])
        self.assertEqual(tuple(graphql_error.path), ("input", "expectedDefinitionRevision"))
        self.assertEqual(rest_error["path"], ["expected_definition_revision"])

    def test_success_result_keeps_same_owner_and_revision_contract(self):
        result = OwnerChangedDTO(
            outcome="changed",
            owner=OwnerRefDTO(owner_kind="asset", owner_id=11),
            resource_revision="resource-2",
            definition_revision="definition-1",
        )
        with patch.object(mutations, "_owner_model", return_value=object()):
            graphql_payload = mutations._owner_payload(
                result,
                mutations.AssetSpecificationPayload,
                "asset",
            )
        rest_response = command_result_response(result)

        self.assertEqual(graphql_payload.user_errors, ())
        self.assertIsNotNone(graphql_payload.asset)
        self.assertEqual(rest_response.data["outcome"], "changed")
        self.assertEqual(rest_response.data["owner"], {"kind": "asset", "id": 11})
        self.assertEqual(rest_response.data["resource_revision"], "resource-2")
        self.assertEqual(rest_response.data["definition_revision"], "definition-1")

    def test_composition_preview_success_is_real_graphql_and_signed(self):
        from core.schema import schema

        owner = SimpleNamespace(pk=11)
        owner_manager = Mock()
        owner_manager.filter.return_value.first.return_value = owner
        user = SimpleNamespace(pk=1, is_active=True)
        actor = ActorContextDTO(actor_id=ActorId(1), authentication_revision="auth-1")
        definition = SimpleNamespace(revision="definition-2")
        query = """
        mutation {
          previewAssetTypeComposition(assetTypeId: "11", fieldsets: ["acme/base"]) {
            token
            definitionRevision
            issues { code }
          }
        }
        """

        with (
            patch.object(mutations, "_actor", return_value=actor),
            patch.object(mutations, "authenticated_user", return_value=user),
            patch.object(mutations, "has_global_model_permission", return_value=True),
            patch.object(mutations, "AssetType") as asset_type,
            patch.object(mutations, "stored_values_for", return_value={}),
            patch.object(mutations, "load_prospective_definition", return_value=(definition, (), ())),
            patch.object(mutations, "owner_resource_revision", return_value="resource-2"),
            patch.object(mutations, "_preview_token_key", return_value="unit-test-secret"),
        ):
            asset_type.all_objects = owner_manager
            result = schema.execute(query, context_value=SimpleNamespace(user=user))

        self.assertIsNone(result.errors)
        self.assertIsNotNone(result.data)
        payload = result.data["previewAssetTypeComposition"]
        self.assertTrue(payload["token"])
        self.assertEqual(payload["definitionRevision"], "definition-2")
        self.assertEqual(payload["issues"], [])

    def test_composition_preview_rejection_is_top_level_graphql_error(self):
        from core.schema import schema

        owner = SimpleNamespace(pk=11)
        owner_manager = Mock()
        owner_manager.filter.return_value.first.return_value = owner
        user = SimpleNamespace(pk=1, is_active=True)
        actor = ActorContextDTO(actor_id=ActorId(1), authentication_revision="auth-1")
        query = """
        mutation {
          previewAssetTypeComposition(assetTypeId: "11", fieldsets: ["acme/missing"]) {
            token
            definitionRevision
          }
        }
        """

        with (
            patch.object(mutations, "_actor", return_value=actor),
            patch.object(mutations, "authenticated_user", return_value=user),
            patch.object(mutations, "has_global_model_permission", return_value=True),
            patch.object(mutations, "AssetType") as asset_type,
            patch.object(mutations, "stored_values_for", return_value={}),
            patch.object(mutations, "load_prospective_definition", side_effect=ValueError("not a definition")),
        ):
            asset_type.all_objects = owner_manager
            result = schema.execute(query, context_value=SimpleNamespace(user=user))

        self.assertIsNotNone(result.errors)
        self.assertIsNone(result.data)
        self.assertEqual(result.errors[0].extensions["code"], "OBJECT_UNAVAILABLE")

    def test_native_update_rejects_explicit_null_without_legacy_type_lookup(self):
        from assets import schema as asset_schema

        check = Mock(return_value=object())
        legacy_lookup = Mock()
        with (
            patch.object(asset_schema, "check_permission", check),
            patch.object(asset_schema, "get_object_or_denied", legacy_lookup),
        ):
            with self.assertRaises(mutations.GraphQLError) as context:
                asset_schema.UpdateAsset.mutate(None, object(), "11", asset_type_id=None)

        self.assertEqual(context.exception.extensions["code"], "INVALID_TYPE")
        self.assertEqual(context.exception.extensions["path"], ["assetTypeId"])
        legacy_lookup.assert_not_called()

    def test_native_create_rejects_explicit_null_without_direct_type_write(self):
        from assets import schema as asset_schema

        check = Mock(return_value=object())
        legacy_lookup = Mock()
        with (
            patch.object(asset_schema, "check_permission", check),
            patch.object(asset_schema, "get_object_or_denied", legacy_lookup),
        ):
            with self.assertRaises(mutations.GraphQLError) as context:
                asset_schema.CreateAsset.mutate(None, object(), name="Laptop", asset_type_id=None)

        self.assertEqual(context.exception.extensions["code"], "INVALID_TYPE")
        self.assertEqual(context.exception.extensions["path"], ["assetTypeId"])
        legacy_lookup.assert_not_called()

    def test_field_definition_uses_existing_typed_command(self):
        command = Mock(return_value=mutations._definition_rejection(mutations._issue("REFERENCE_CONFLICT")))
        actor = ActorContextDTO(actor_id=ActorId(1), authentication_revision="auth-1")
        input_value = {
            "key": "rack_owner",
            "namespace": "acme",
            "label": "Rack owner",
            "help_text": "",
            "targets": ["asset_type"],
            "activation": "composed",
            "field_type": "text",
            "required": False,
            "nullable": False,
            "validation": {},
        }

        with (
            patch.object(mutations, "_actor", return_value=actor),
            patch.object(mutations, "create_custom_field", command),
        ):
            mutations.CreateSpecificationField.mutate(None, object(), input_value)

        command.assert_called_once()
        definition = command.call_args.kwargs["definition"]
        self.assertEqual(definition.namespace, "acme")
        self.assertEqual(definition.local_key, "rack_owner")
        self.assertEqual(definition.object_types, ("assets.assettype",))
        self.assertEqual(definition.field_type, "text")

    def test_library_preview_passes_resolutions_to_successor_command(self):
        commands = Mock()
        commands.preview_library.return_value = SimpleNamespace(
            preview_token="signed-plan",
            plan=SimpleNamespace(plan_digest="digest", can_apply=True, actions=()),
        )
        actor = SimpleNamespace(pk=1, is_active=True)
        input_value = {
            "document_text": "{}",
            "resolutions": [{"action_id": "action-1", "decision": "take_upstream"}],
        }

        with (
            patch.object(mutations, "authenticated_user", return_value=actor),
            patch.object(mutations, "library_commands", commands),
            patch.object(mutations, "_preview_token_key", return_value="unit-test-secret"),
        ):
            mutations.PreviewLibrary.mutate(None, object(), input_value)

        commands.preview_library.assert_called_once_with(
            "{}",
            actor=actor,
            signing_key="unit-test-secret",
            resolutions={"action-1": "take_upstream"},
        )


if __name__ == "__main__":
    unittest.main()
