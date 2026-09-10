import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from graphql import GraphQLError

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


class GraphQLSpecificationCommandAdapterTests(unittest.TestCase):
    pytestmark = pytest.mark.django_db

    """Command-adapter behaviour for the field/fieldset/choice/history surfaces.

    Every test drives the public mutation adapter and asserts the exact command
    invocation, the rendered rejection code/path and the preserved values.  The
    domain commands are replaced inside the adapter module so that the assertion
    is about adapter transport behaviour, not about a second write path.
    """

    ACTOR = ActorContextDTO(actor_id=ActorId(1), authentication_revision="auth-1")

    def _adapter(self, **extra):
        patchers = [
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
        ]
        for name, value in extra.items():
            patchers.append(patch.object(mutations, name, **value))
        return patchers

    # -- choice sets ------------------------------------------------------

    def test_create_choice_set_creates_choices_in_declared_order(self):
        set_command = Mock(return_value=SimpleNamespace(definition_id=70))
        choice_command = Mock(side_effect=[SimpleNamespace(definition_id=71), SimpleNamespace(definition_id=72)])
        input_value = {
            "identity": "acme/colors",
            "label": "Colors",
            "choices": [{"key": "red", "label": "Red"}, {"key": "blue", "label": "Blue"}],
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "create_custom_field_choice_set", set_command),
            patch.object(mutations, "create_custom_field_choice", choice_command),
        ):
            result = mutations.CreateChoiceSet.mutate(None, object(), input_value)

        definition = set_command.call_args.kwargs["definition"]
        self.assertEqual((definition.namespace, definition.slug, definition.label), ("acme", "colors", "Colors"))
        self.assertEqual(set_command.call_args.kwargs["actor"], self.ACTOR)
        created = [call.kwargs["definition"] for call in choice_command.call_args_list]
        self.assertEqual([item.position for item in created], [1, 2])
        self.assertEqual([item.key for item in created], ["red", "blue"])
        self.assertEqual([item.label for item in created], ["Red", "Blue"])
        self.assertEqual([item.choice_set_id for item in created], [70, 70])
        self.assertIs(result, set_command.return_value)

    def test_create_choice_set_aborts_after_rejected_choice_without_further_writes(self):
        rejected = mutations._definition_rejection(mutations._issue("REFERENCE_CONFLICT"))
        set_command = Mock(return_value=SimpleNamespace(definition_id=70))
        choice_command = Mock(side_effect=[SimpleNamespace(definition_id=71), rejected])
        input_value = {
            "identity": "acme/colors",
            "label": "Colors",
            "choices": [
                {"key": "red", "label": "Red"},
                {"key": "blue", "label": "Blue"},
                {"key": "green", "label": "Green"},
            ],
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "create_custom_field_choice_set", set_command),
            patch.object(mutations, "create_custom_field_choice", choice_command),
        ):
            result = mutations.CreateChoiceSet.mutate(None, object(), input_value)

        self.assertIs(result, rejected)
        self.assertEqual(choice_command.call_count, 2)
        self.assertEqual([call.kwargs["definition"].key for call in choice_command.call_args_list], ["red", "blue"])

    def test_create_choice_set_renders_invalid_choices_without_dispatching(self):
        set_command = Mock()
        choice_command = Mock()
        input_value = {"identity": "acme/colors", "label": "Colors", "choices": "red"}

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "create_custom_field_choice_set", set_command),
            patch.object(mutations, "create_custom_field_choice", choice_command),
        ):
            payload = mutations.CreateChoiceSet.mutate(None, object(), input_value)

        self.assertIsNone(payload.choice_set)
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "choices"])
        set_command.assert_not_called()
        choice_command.assert_not_called()

    def test_create_choice_set_maps_unexpected_command_failure_to_invalid_type(self):
        set_command = Mock(side_effect=TypeError("bad definition"))
        input_value = {"identity": "acme/colors", "label": "Colors", "choices": []}

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "create_custom_field_choice_set", set_command),
            patch.object(mutations, "create_custom_field_choice", Mock()),
        ):
            payload = mutations.CreateChoiceSet.mutate(None, object(), input_value)

        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input"])

    def test_update_choice_set_lifecycle_selects_the_right_command(self):
        cases = (
            ("deprecated", None, "deprecate_custom_field_choice_set"),
            (None, "Renamed", "update_custom_field_choice_set"),
        )
        for lifecycle, label, expected_command in cases:
            with self.subTest(lifecycle=lifecycle, label=label):
                model = SimpleNamespace(pk=5, lifecycle="active")
                commands = {
                    name: Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
                    for name in (
                        "deprecate_custom_field_choice_set",
                        "update_custom_field_choice_set",
                    )
                }
                input_value = {"identity": "acme/colors", "expected_resource_revision": "rev-1"}
                if lifecycle is not None:
                    input_value["lifecycle"] = lifecycle
                if label is not None:
                    input_value["label"] = label

                with (
                    patch.object(mutations, "_actor", return_value=self.ACTOR),
                    patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
                    patch.object(mutations, "_definition_model", return_value=model),
                    patch.object(
                        mutations, "deprecate_custom_field_choice_set", commands["deprecate_custom_field_choice_set"]
                    ),
                    patch.object(
                        mutations, "update_custom_field_choice_set", commands["update_custom_field_choice_set"]
                    ),
                ):
                    mutations.UpdateChoiceSet.mutate(None, object(), input_value)

                for name, command in commands.items():
                    if name == expected_command:
                        command.assert_called_once()
                        self.assertEqual(command.call_args.kwargs["choice_set_id"], 5)
                        self.assertEqual(command.call_args.kwargs["expected_resource_revision"], "rev-1")
                    else:
                        command.assert_not_called()

    def test_update_choice_set_rejects_policy_change_on_deprecation(self):
        for label in ("Renamed", None):
            with self.subTest(label=label):
                update_command = Mock()
                deprecate_command = Mock()
                model = SimpleNamespace(pk=5, lifecycle="active")
                input_value = {
                    "identity": "acme/colors",
                    "expected_resource_revision": "rev-1",
                    "lifecycle": "deprecated",
                }
                if label is not None:
                    input_value["label"] = label

                with (
                    patch.object(mutations, "_actor", return_value=self.ACTOR),
                    patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
                    patch.object(mutations, "_definition_model", return_value=model),
                    patch.object(mutations, "deprecate_custom_field_choice_set", deprecate_command),
                    patch.object(mutations, "update_custom_field_choice_set", update_command),
                ):
                    result = mutations.UpdateChoiceSet.mutate(None, object(), input_value)

                if label is None:
                    deprecate_command.assert_called_once()
                    update_command.assert_not_called()
                else:
                    self.assertEqual([issue.code for issue in result.issues], ["UNSUPPORTED_STRUCTURE"])
                    self.assertEqual([issue.path for issue in result.issues], [()])
                    deprecate_command.assert_not_called()
                    update_command.assert_not_called()

    def test_update_choice_set_blocks_reactivation_of_deprecated_set(self):
        command = Mock()
        model = SimpleNamespace(pk=5, lifecycle="deprecated")
        input_value = {
            "identity": "acme/colors",
            "expected_resource_revision": "rev-1",
            "lifecycle": "active",
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=model),
            patch.object(mutations, "update_custom_field_choice_set", command),
        ):
            payload = mutations.UpdateChoiceSet.mutate(None, object(), input_value)

        self.assertEqual([error.code for error in payload.user_errors], ["IMMUTABLE_DEFINITION"])
        command.assert_not_called()

    def test_update_choice_set_rejects_impact_token_and_unresolved_identity(self):
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "update_custom_field_choice_set", Mock()),
        ):
            payload = mutations.UpdateChoiceSet.mutate(
                None,
                object(),
                {
                    "identity": "acme/colors",
                    "expected_resource_revision": "rev-1",
                    "impact_token": "signed-plan",
                },
            )
        self.assertEqual([error.code for error in payload.user_errors], ["UNSUPPORTED_STRUCTURE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "impactToken"])

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=None),
            patch.object(mutations, "update_custom_field_choice_set", Mock()),
        ):
            payload = mutations.UpdateChoiceSet.mutate(
                None,
                object(),
                {"identity": "acme/colors", "expected_resource_revision": "rev-1"},
            )
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])
        self.assertIsNone(payload.choice_set)

    # -- choices ----------------------------------------------------------

    def test_add_choice_appends_after_the_highest_position(self):
        choice_set = SimpleNamespace(pk=9, choices=Mock())
        choice_set.choices.order_by.return_value.values_list.return_value.first.return_value = 4
        command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
        input_value = {
            "choice_set": "acme/colors",
            "expected_resource_revision": "rev-4",
            "choice": {"key": "green", "label": "Green"},
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "resource_revision_for_definition", return_value="rev-4"),
            patch.object(mutations, "create_custom_field_choice", command),
        ):
            mutations.AddChoice.mutate(None, object(), input_value)

        definition = command.call_args.kwargs["definition"]
        self.assertEqual(definition.choice_set_id, 9)
        self.assertEqual(definition.position, 5)
        self.assertEqual((definition.key, definition.label), ("green", "Green"))

    def test_add_choice_rejects_stale_choice_set_without_writing(self):
        choice_set = SimpleNamespace(pk=9, choices=Mock())
        choice_set.choices.order_by.return_value.values_list.return_value.first.return_value = 4
        command = Mock()
        input_value = {
            "choice_set": "acme/colors",
            "expected_resource_revision": "rev-9",
            "choice": {"key": "green", "label": "Green"},
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "resource_revision_for_definition", return_value="rev-4"),
            patch.object(mutations, "create_custom_field_choice", command),
        ):
            result = mutations.AddChoice.mutate(None, object(), input_value)

        self.assertEqual([issue.code for issue in result.issues], ["STALE_RESOURCE"])
        command.assert_not_called()

    def test_add_choice_rejects_empty_or_missing_choice_key(self):
        choice_set = SimpleNamespace(pk=9, choices=Mock())
        choice_set.choices.order_by.return_value.values_list.return_value.first.return_value = None
        command = Mock()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "resource_revision_for_definition", return_value="rev-4"),
            patch.object(mutations, "create_custom_field_choice", command),
        ):
            payload = mutations.AddChoice.mutate(
                None,
                object(),
                {
                    "choice_set": "acme/colors",
                    "expected_resource_revision": "rev-4",
                    "choice": {"key": "", "label": "Green"},
                },
            )

        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "choice", "key"])
        command.assert_not_called()

    def test_update_choice_handles_deprecate_reactivate_and_relabel(self):
        choice = SimpleNamespace(pk=33, lifecycle="deprecated")
        update_command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
        deprecate_command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))

        def run(**overrides):
            input_value = {
                "choice_set": "acme/colors",
                "key": "red",
                "expected_resource_revision": "rev-1",
            }
            input_value.update(overrides)
            with (
                patch.object(mutations, "_actor", return_value=self.ACTOR),
                patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
                patch.object(mutations, "_definition_model", return_value=choice),
                patch.object(mutations, "update_custom_field_choice", update_command),
                patch.object(mutations, "deprecate_custom_field_choice", deprecate_command),
            ):
                return mutations.UpdateChoice.mutate(None, object(), input_value)

        update_command.reset_mock()
        deprecate_command.reset_mock()
        run(lifecycle="deprecated")
        self.assertEqual(deprecate_command.call_args.kwargs["choice_id"], 33)
        self.assertEqual(deprecate_command.call_args.kwargs["expected_resource_revision"], "rev-1")
        update_command.assert_not_called()

        update_command.reset_mock()
        deprecate_command.reset_mock()
        result = run(lifecycle="deprecated", label="Crimson")
        self.assertEqual([issue.code for issue in result.issues], ["UNSUPPORTED_STRUCTURE"])
        self.assertEqual([issue.path for issue in result.issues], [()])
        update_command.assert_not_called()
        deprecate_command.assert_not_called()

        update_command.reset_mock()
        result = run(lifecycle="active")
        self.assertEqual([issue.code for issue in result.issues], ["IMMUTABLE_DEFINITION"])
        update_command.assert_not_called()

        update_command.reset_mock()
        run(label="Crimson")
        self.assertEqual(update_command.call_args.kwargs["choice_id"], 33)
        self.assertEqual(update_command.call_args.kwargs["changes"].label, "Crimson")
        self.assertEqual(update_command.call_args.kwargs["changes"].position, None)

    def test_reorder_choices_validates_keys_before_any_definition_lookup(self):
        cases = (
            ("not-a-list", "INVALID_TYPE", ("input", "keys")),
            (["red", "red"], "DUPLICATE_FIELD", ("input", "keys")),
            (["red", 7], "INVALID_TYPE", ("input", "keys")),
        )
        for keys, code, path in cases:
            with self.subTest(keys=keys):
                model = Mock()
                update_command = Mock()
                with (
                    patch.object(mutations, "_actor", return_value=self.ACTOR),
                    patch.object(mutations, "_definition_model", model),
                    patch.object(mutations, "update_custom_field_choice", update_command),
                ):
                    payload = mutations.ReorderChoices.mutate(
                        None,
                        object(),
                        {"choice_set": "acme/colors", "expected_resource_revision": "rev-1", "keys": keys},
                    )
                self.assertEqual([error.code for error in payload.user_errors], [code])
                self.assertEqual(list(payload.user_errors[0].path), list(path))
                update_command.assert_not_called()
                model.assert_not_called()

    def test_reorder_choices_rejects_key_sets_that_do_not_match_the_choice_set(self):
        choice_set = SimpleNamespace(pk=8)
        choice_set.choices = Mock()
        choice_set.choices.all.return_value = [
            SimpleNamespace(pk=1, key="red"),
            SimpleNamespace(pk=2, key="blue"),
        ]
        update_command = Mock()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "update_custom_field_choice", update_command),
        ):
            payload = mutations.ReorderChoices.mutate(
                None,
                object(),
                {"choice_set": "acme/colors", "expected_resource_revision": "rev-1", "keys": ["red", "green"]},
            )

        self.assertEqual([error.code for error in payload.user_errors], ["REFERENCE_CONFLICT"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "keys"])
        update_command.assert_not_called()

    def test_reorder_choices_rewrites_positions_then_bumps_the_choice_set(self):
        choice_set = SimpleNamespace(pk=8, lifecycle="active")
        choice_set.choices = Mock()
        choice_set.choices.all.return_value = [
            SimpleNamespace(pk=1, key="red"),
            SimpleNamespace(pk=2, key="blue"),
        ]
        update_command = Mock(side_effect=[SimpleNamespace(definition_id=1), SimpleNamespace(definition_id=2)])
        set_command = Mock(return_value=SimpleNamespace(definition_id=8))

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "resource_revision_for_definition", return_value="rev-1"),
            patch.object(mutations, "update_custom_field_choice", update_command),
            patch.object(mutations, "update_custom_field_choice_set", set_command),
        ):
            result = mutations.ReorderChoices.mutate(
                None,
                object(),
                {"choice_set": "acme/colors", "expected_resource_revision": "rev-1", "keys": ["blue", "red"]},
            )

        self.assertEqual([call.kwargs["choice_id"] for call in update_command.call_args_list], [2, 1])
        self.assertEqual(
            [call.kwargs["changes"].position for call in update_command.call_args_list],
            [1, 2],
        )
        self.assertEqual(
            [call.kwargs["expected_resource_revision"] for call in update_command.call_args_list],
            ["rev-1", "rev-1"],
        )
        set_command.assert_called_once()
        self.assertEqual(set_command.call_args.kwargs["choice_set_id"], 8)
        self.assertIs(result, set_command.return_value)

    def test_reorder_choices_stops_when_the_choice_set_revision_moved(self):
        choice_set = SimpleNamespace(pk=8, lifecycle="active")
        choice_set.choices = Mock()
        choice_set.choices.all.return_value = [SimpleNamespace(pk=1, key="red")]
        update_command = Mock()
        set_command = Mock()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "_definition_model", return_value=choice_set),
            patch.object(mutations, "resource_revision_for_definition", return_value="rev-2"),
            patch.object(mutations, "update_custom_field_choice", update_command),
            patch.object(mutations, "update_custom_field_choice_set", set_command),
        ):
            result = mutations.ReorderChoices.mutate(
                None,
                object(),
                {"choice_set": "acme/colors", "expected_resource_revision": "rev-1", "keys": ["red"]},
            )

        self.assertEqual([issue.code for issue in result.issues], ["STALE_RESOURCE"])
        update_command.assert_not_called()
        set_command.assert_not_called()

    # -- history cleanup --------------------------------------------------

    def test_preview_history_cleanup_requires_asset_authorization(self):
        with patch.object(mutations, "_asset_authorization", return_value=None):
            with self.assertRaises(GraphQLError) as context:
                mutations.PreviewSpecificationHistoryCleanup.mutate(
                    None,
                    object(),
                    **{"owner_id": "11", "target": "asset", "keys": ["serial"]},
                )

        self.assertEqual(context.exception.extensions["code"], "OBJECT_UNAVAILABLE")

    def test_preview_history_cleanup_dispatches_asset_command_with_revisions(self):
        command = Mock(
            return_value=SimpleNamespace(
                preview_token="signed-plan",
                expected_definition_revision="definition-2",
                issues=(),
            )
        )
        with (
            patch.object(mutations, "_asset_authorization", return_value="authorization"),
            patch.object(mutations, "_history_revisions", return_value=("resource-1", "definition-2")),
            patch.object(mutations, "preview_asset_history_cleanup", command),
        ):
            preview = mutations.PreviewSpecificationHistoryCleanup.mutate(
                None,
                object(),
                **{"owner_id": "11", "target": "asset", "keys": ["serial", "mac"]},
            )

        self.assertEqual(command.call_args.kwargs["authorization"], "authorization")
        self.assertEqual(command.call_args.kwargs["asset_id"], 11)
        self.assertEqual(command.call_args.kwargs["keys"], ("serial", "mac"))
        self.assertEqual(command.call_args.kwargs["expected_resource_revision"], "resource-1")
        self.assertEqual(command.call_args.kwargs["expected_definition_revision"], "definition-2")
        self.assertEqual(preview.token, "signed-plan")
        self.assertEqual(preview.definition_revision, "definition-2")
        self.assertEqual(preview.issues, ())

    def test_preview_history_cleanup_asset_type_rejects_requested_scope(self):
        with self.assertRaises(GraphQLError) as context:
            mutations.PreviewSpecificationHistoryCleanup.mutate(
                None,
                object(),
                **{
                    "owner_id": "11",
                    "target": "asset_type",
                    "keys": ["serial"],
                    "requested_scope": {"kind": "tenant", "tenant_id": "7"},
                },
            )

        self.assertEqual(context.exception.extensions["code"], "INVALID_TYPE")
        self.assertEqual(context.exception.extensions["path"], ["requestedScope"])

    def test_preview_history_cleanup_asset_type_dispatches_actor_command(self):
        command = Mock(
            return_value=SimpleNamespace(
                preview_token="signed-plan",
                expected_definition_revision="definition-2",
                issues=(),
            )
        )
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_history_revisions", return_value=("resource-1", "definition-2")),
            patch.object(mutations, "preview_asset_type_history_cleanup", command),
        ):
            mutations.PreviewSpecificationHistoryCleanup.mutate(
                None,
                object(),
                **{"owner_id": "11", "target": "asset_type", "keys": ["serial"]},
            )

        self.assertEqual(command.call_args.kwargs["actor"], self.ACTOR)
        self.assertEqual(command.call_args.kwargs["asset_type_id"], 11)
        self.assertNotIn("authorization", command.call_args.kwargs)

    def test_preview_history_cleanup_maps_rejection_and_missing_token_to_graphql_error(self):
        rejected = CommandRejectedDTO(
            outcome="rejected",
            safe_owner=None,
            issues=(mutations._issue("STALE_RESOURCE"),),
        )
        with (
            patch.object(mutations, "_asset_authorization", return_value="authorization"),
            patch.object(mutations, "_history_revisions", return_value=("resource-1", "definition-2")),
            patch.object(mutations, "preview_asset_history_cleanup", Mock(return_value=rejected)),
        ):
            with self.assertRaises(GraphQLError) as context:
                mutations.PreviewSpecificationHistoryCleanup.mutate(
                    None,
                    object(),
                    **{"owner_id": "11", "target": "asset", "keys": ["serial"]},
                )
        self.assertEqual(context.exception.extensions["code"], "STALE_RESOURCE")
        self.assertEqual(context.exception.extensions["path"], ["expectedResourceRevision"])

        tokenless = SimpleNamespace(
            preview_token=None,
            expected_definition_revision="definition-2",
            issues=(),
        )
        with (
            patch.object(mutations, "_asset_authorization", return_value="authorization"),
            patch.object(mutations, "_history_revisions", return_value=("resource-1", "definition-2")),
            patch.object(mutations, "preview_asset_history_cleanup", Mock(return_value=tokenless)),
        ):
            with self.assertRaises(GraphQLError) as context:
                mutations.PreviewSpecificationHistoryCleanup.mutate(
                    None,
                    object(),
                    **{"owner_id": "11", "target": "asset", "keys": ["serial"]},
                )
        self.assertEqual(context.exception.extensions["code"], "OBJECT_UNAVAILABLE")

    def test_cleanup_history_requires_token_revisions_and_valid_target(self):
        command = Mock()
        with (
            patch.object(mutations, "_asset_authorization", return_value="authorization"),
            patch.object(mutations, "cleanup_asset_history", command),
        ):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "asset",
                    "owner_id": "11",
                    "keys": ["serial"],
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                },
            )
        self.assertEqual(payload.removed_keys, ())
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "previewToken"])
        command.assert_not_called()

        with patch.object(mutations, "cleanup_asset_history", command):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "unsupported",
                    "owner_id": "11",
                    "keys": ["serial"],
                    "preview_token": "signed-plan",
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                },
            )
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "target"])
        command.assert_not_called()

    def test_cleanup_history_dispatches_per_target_and_reports_removed_keys(self):
        asset_keys = ("serial", "mac")
        asset_result = OwnerChangedDTO(
            outcome="changed",
            owner=OwnerRefDTO(owner_kind="asset", owner_id=11),
            resource_revision="resource-2",
            definition_revision="definition-2",
        )
        asset_command = Mock(return_value=asset_result)
        with (
            patch.object(mutations, "_asset_authorization", return_value="authorization"),
            patch.object(mutations, "cleanup_asset_history", asset_command),
        ):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "asset",
                    "owner_id": "11",
                    "keys": list(asset_keys),
                    "preview_token": "signed-plan",
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                },
            )
        self.assertEqual(payload.removed_keys, tuple(asset_keys))
        self.assertEqual(payload.user_errors, ())
        self.assertEqual(asset_command.call_args.kwargs["keys"], asset_keys)
        self.assertEqual(asset_command.call_args.kwargs["preview_token"], "signed-plan")
        self.assertEqual(asset_command.call_args.kwargs["asset_id"], 11)
        self.assertEqual(asset_command.call_args.kwargs["authorization"], "authorization")

        type_command = Mock(return_value=asset_result)
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "cleanup_asset_type_history", type_command),
        ):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "asset_type",
                    "owner_id": "11",
                    "keys": ["serial"],
                    "preview_token": "signed-plan",
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                },
            )
        self.assertEqual(payload.removed_keys, ("serial",))
        self.assertEqual(type_command.call_args.kwargs["actor"], self.ACTOR)
        self.assertEqual(type_command.call_args.kwargs["asset_type_id"], 11)

    def test_cleanup_history_rejects_asset_scope_and_denied_authorization(self):
        command = Mock()
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "cleanup_asset_type_history", command),
        ):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "asset_type",
                    "owner_id": "11",
                    "keys": ["serial"],
                    "preview_token": "signed-plan",
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                    "requested_scope": {"kind": "tenant", "tenant_id": "7"},
                },
            )
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "requestedScope"])
        command.assert_not_called()

        with (
            patch.object(mutations, "_asset_authorization", return_value=None),
            patch.object(mutations, "cleanup_asset_history", command),
        ):
            payload = mutations.CleanupSpecificationHistory.mutate(
                None,
                object(),
                {
                    "target": "asset",
                    "owner_id": "11",
                    "keys": ["serial"],
                    "preview_token": "signed-plan",
                    "expected_resource_revision": "resource-1",
                    "expected_definition_revision": "definition-2",
                },
            )
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])
        self.assertEqual(payload.removed_keys, ())
        command.assert_not_called()

    # -- category defaults ------------------------------------------------

    def test_apply_category_defaults_requires_every_revision_and_forwards_the_patch(self):
        command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
        base = {
            "asset_type_id": "11",
            "expected_resource_revision": "resource-1",
            "expected_definition_revision": "definition-2",
            "expected_category_default_snapshot_revision": "snapshot-3",
            "preview_token": "signed-plan",
            "patch": {"set": [{"key": "hostname", "value": {"text": "rack-1"}}], "clear": []},
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "apply_category_defaults", command),
        ):
            incomplete = dict(base)
            del incomplete["expected_category_default_snapshot_revision"]
            payload = mutations.ApplyCategoryDefaults.mutate(None, object(), incomplete)
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(
            list(payload.user_errors[0].path),
            ["input", "expectedCategoryDefaultSnapshotRevision"],
        )
        command.assert_not_called()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_owner_payload", return_value="rendered"),
            patch.object(mutations, "apply_category_defaults", command),
        ):
            rendered = mutations.ApplyCategoryDefaults.mutate(None, object(), base)

        self.assertEqual(rendered, "rendered")
        kwargs = command.call_args.kwargs
        self.assertEqual(kwargs["actor"], self.ACTOR)
        self.assertEqual(kwargs["asset_type_id"], 11)
        self.assertEqual(kwargs["preview_token"], "signed-plan")
        self.assertEqual(kwargs["expected_resource_revision"], "resource-1")
        self.assertEqual(kwargs["expected_definition_revision"], "definition-2")
        self.assertEqual(kwargs["expected_category_default_snapshot_revision"], "snapshot-3")
        self.assertEqual(dict(kwargs["patch"].set_values), {"hostname": "rack-1"})
        self.assertEqual(kwargs["patch"].clear_keys, ())

    def test_preview_apply_category_defaults_reports_input_errors_and_dispatches(self):
        command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "preview_apply_category_defaults", command),
        ):
            payload = mutations.PreviewApplyCategoryDefaults.mutate(
                None,
                object(),
                {
                    "asset_type_id": "0",
                    "expected_resource_revision": "resource-1",
                    "patch": {"set": [], "clear": []},
                },
            )
        self.assertIsNone(payload.preview)
        self.assertEqual([error.code for error in payload.user_errors], ["INVALID_TYPE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "assetTypeId"])
        command.assert_not_called()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_preview_payload", return_value="preview-payload"),
            patch.object(mutations, "preview_apply_category_defaults", command),
        ):
            rendered = mutations.PreviewApplyCategoryDefaults.mutate(
                None,
                object(),
                {
                    "asset_type_id": 11,
                    "expected_resource_revision": "resource-1",
                    "patch": {"set": [], "clear": []},
                },
            )

        self.assertEqual(rendered, "preview-payload")
        self.assertEqual(command.call_args.kwargs["asset_type_id"], 11)
        self.assertEqual(command.call_args.kwargs["expected_resource_revision"], "resource-1")

    # -- fields and fieldsets ---------------------------------------------

    def test_update_specification_fieldset_selects_membership_or_policy_command(self):
        model = SimpleNamespace(pk=6, lifecycle="active")
        commands = {
            name: Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
            for name in (
                "deprecate_custom_fieldset",
                "replace_custom_fieldset_memberships",
                "update_custom_fieldset",
            )
        }

        def run(**overrides):
            input_value = {
                "identity": "acme/identity",
                "expected_resource_revision": "rev-1",
            }
            input_value.update(overrides)
            for command in commands.values():
                command.reset_mock()
            with (
                patch.object(mutations, "_actor", return_value=self.ACTOR),
                patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
                patch.object(mutations, "_definition_model", return_value=model),
                patch.object(mutations, "deprecate_custom_fieldset", commands["deprecate_custom_fieldset"]),
                patch.object(
                    mutations,
                    "replace_custom_fieldset_memberships",
                    commands["replace_custom_fieldset_memberships"],
                ),
                patch.object(mutations, "update_custom_fieldset", commands["update_custom_fieldset"]),
            ):
                return mutations.UpdateSpecificationFieldset.mutate(None, object(), input_value)

        run(lifecycle="deprecated")
        commands["deprecate_custom_fieldset"].assert_called_once()
        self.assertEqual(commands["deprecate_custom_fieldset"].call_args.kwargs["fieldset_id"], 6)
        commands["update_custom_fieldset"].assert_not_called()

        result = run(lifecycle="deprecated", label="Renamed")
        self.assertEqual([issue.code for issue in result.issues], ["UNSUPPORTED_STRUCTURE"])

        run(fields=["acme/a", "acme/b"])
        self.assertEqual(
            commands["replace_custom_fieldset_memberships"].call_args.kwargs["field_identities"],
            ("acme/a", "acme/b"),
        )
        commands["update_custom_fieldset"].assert_not_called()

        result = run(fields=["acme/a"], label="Renamed")
        self.assertEqual([issue.code for issue in result.issues], ["UNSUPPORTED_STRUCTURE"])
        commands["replace_custom_fieldset_memberships"].assert_not_called()

        run(description="Structured identity data")
        self.assertEqual(
            commands["update_custom_fieldset"].call_args.kwargs["changes"].description,
            "Structured identity data",
        )
        self.assertIsNone(commands["update_custom_fieldset"].call_args.kwargs["changes"].label)

    def test_update_specification_fieldset_blocks_reactivation_and_scope_errors(self):
        model = SimpleNamespace(pk=6, lifecycle="deprecated")
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=model),
            patch.object(mutations, "update_custom_fieldset", Mock()),
        ):
            payload = mutations.UpdateSpecificationFieldset.mutate(
                None,
                object(),
                {"identity": "acme/identity", "expected_resource_revision": "rev-1", "lifecycle": "active"},
            )
        self.assertEqual([error.code for error in payload.user_errors], ["IMMUTABLE_DEFINITION"])

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=None),
            patch.object(mutations, "update_custom_fieldset", Mock()),
        ):
            payload = mutations.UpdateSpecificationFieldset.mutate(
                None,
                object(),
                {"identity": "acme/identity", "expected_resource_revision": "rev-1"},
            )
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])

    def test_update_specification_field_policy_deprecates_and_preserves_false_required(self):
        model = SimpleNamespace(pk=4, lifecycle="active")
        update_command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
        deprecate_command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))

        def run(**overrides):
            input_value = {
                "identity": "acme/rack_owner",
                "expected_resource_revision": "rev-1",
            }
            input_value.update(overrides)
            update_command.reset_mock()
            deprecate_command.reset_mock()
            with (
                patch.object(mutations, "_actor", return_value=self.ACTOR),
                patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
                patch.object(mutations, "_definition_model", return_value=model),
                patch.object(mutations, "update_custom_field", update_command),
                patch.object(mutations, "deprecate_custom_field", deprecate_command),
            ):
                return mutations.UpdateSpecificationFieldPolicy.mutate(None, object(), input_value)

        run(lifecycle="deprecated")
        self.assertEqual(deprecate_command.call_args.kwargs["field_id"], 4)
        update_command.assert_not_called()

        result = run(lifecycle="deprecated", label="Renamed")
        self.assertEqual([issue.code for issue in result.issues], ["UNSUPPORTED_STRUCTURE"])
        update_command.assert_not_called()
        deprecate_command.assert_not_called()

        result = run(required=False)
        self.assertFalse(update_command.call_args.kwargs["changes"].required)
        self.assertEqual(update_command.call_args.kwargs["changes"].label, None)

        run(label="Rack owner", help_text="Who owns it", required=False, activation="composed")
        changes = update_command.call_args.kwargs["changes"]
        self.assertEqual(changes.label, "Rack owner")
        self.assertEqual(changes.help_text, "Who owns it")
        self.assertEqual(changes.activation, "composed")
        self.assertIs(changes.required, False)

    def test_update_specification_field_policy_blocks_reactivation_and_impact_tokens(self):
        deprecated = SimpleNamespace(pk=4, lifecycle="deprecated")
        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=deprecated),
            patch.object(mutations, "update_custom_field", Mock()),
        ):
            payload = mutations.UpdateSpecificationFieldPolicy.mutate(
                None,
                object(),
                {"identity": "acme/rack_owner", "expected_resource_revision": "rev-1", "lifecycle": "active"},
            )
        self.assertEqual([error.code for error in payload.user_errors], ["IMMUTABLE_DEFINITION"])

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=deprecated),
            patch.object(mutations, "update_custom_field", Mock()),
        ):
            payload = mutations.UpdateSpecificationFieldPolicy.mutate(
                None,
                object(),
                {
                    "identity": "acme/rack_owner",
                    "expected_resource_revision": "rev-1",
                    "impact_token": "signed-plan",
                },
            )
        self.assertEqual([error.code for error in payload.user_errors], ["UNSUPPORTED_STRUCTURE"])
        self.assertEqual(list(payload.user_errors[0].path), ["input", "impactToken"])

    def test_create_specification_field_resolves_or_rejects_the_choice_set(self):
        create_command = Mock(return_value=mutations._definition_rejection(mutations._issue("OBJECT_UNAVAILABLE")))
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
            "choice_set": "acme/colors",
        }

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_model", return_value=None),
            patch.object(mutations, "create_custom_field", create_command),
        ):
            payload = mutations.CreateSpecificationField.mutate(None, object(), dict(input_value))
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])
        self.assertIsNone(payload.field)
        create_command.assert_not_called()

        with (
            patch.object(mutations, "_actor", return_value=self.ACTOR),
            patch.object(mutations, "_definition_payload", side_effect=lambda result: result),
            patch.object(mutations, "_definition_model", return_value=SimpleNamespace(pk=12)),
            patch.object(mutations, "create_custom_field", create_command),
        ):
            mutations.CreateSpecificationField.mutate(None, object(), dict(input_value))
        self.assertEqual(create_command.call_args.kwargs["definition"].choice_set_id, 12)

    def test_definition_payload_maps_missing_definitions_and_views(self):
        payload = mutations._definition_payload(object())
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])
        self.assertIsNone(payload.field)

        success = self._definition_success()
        with patch.object(
            mutations, "_definition_view", return_value={"field": None, "fieldset": None, "choice_set": None}
        ):
            payload = mutations._definition_payload(success)
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])

        with patch.object(
            mutations,
            "_definition_view",
            return_value={"field": "field-node", "fieldset": None, "choice_set": None},
        ):
            payload = mutations._definition_payload(success)
        self.assertEqual([error.code for error in payload.user_errors], ["OBJECT_UNAVAILABLE"])

        with patch.object(
            mutations,
            "_definition_view",
            return_value={"field": "field-node", "fieldset": "fieldset-node", "choice_set": "choice-set-node"},
        ):
            payload = mutations._definition_payload(success)
        self.assertEqual(payload.field, "field-node")
        self.assertEqual(payload.fieldset, "fieldset-node")
        self.assertEqual(payload.choice_set, "choice-set-node")
        self.assertEqual(payload.user_errors, ())

    @staticmethod
    def _definition_success():
        from extras.services.definition_command_contracts import DefinitionSuccessDTO

        return DefinitionSuccessDTO(
            outcome="changed",
            definition_kind="field",
            definition_id=12,
            identity="acme/rack_owner",
            resource_revision="rev-2",
            lifecycle="active",
            version=2,
        )

    # -- shared guards ----------------------------------------------------

    def test_impact_token_guard_rejects_any_token_and_allows_absence(self):
        mutations._impact_token_guard(mutations._MISSING, path=("input", "impactToken"))
        mutations._impact_token_guard(None, path=("input", "impactToken"))
        with self.assertRaises(mutations._InputError) as context:
            mutations._impact_token_guard("signed-plan", path=("input", "impactToken"))
        self.assertEqual([issue.code for issue in context.exception.issues], ["UNSUPPORTED_STRUCTURE"])
        self.assertEqual([issue.path for issue in context.exception.issues], [("input", "impactToken")])

    def test_lifecycle_and_target_guards_report_exact_paths(self):
        self.assertIsNone(mutations._lifecycle(mutations._MISSING, path=("input", "lifecycle")))
        self.assertIsNone(mutations._lifecycle(None, path=("input", "lifecycle")))
        self.assertEqual(mutations._lifecycle("deprecated", path=("input", "lifecycle")), "deprecated")
        with self.assertRaises(mutations._InputError) as context:
            mutations._lifecycle("archived", path=("input", "lifecycle"))
        self.assertEqual([issue.code for issue in context.exception.issues], ["INVALID_TYPE"])
        self.assertEqual([issue.path for issue in context.exception.issues], [("input", "lifecycle")])

        self.assertEqual(mutations._target("asset"), "asset")
        self.assertEqual(mutations._target("asset_type"), "asset_type")
        with self.assertRaises(mutations._InputError) as context:
            mutations._target("category")
        self.assertEqual([issue.code for issue in context.exception.issues], ["INVALID_TYPE"])
        self.assertEqual([issue.path for issue in context.exception.issues], [("input", "target")])

    def test_keys_guard_preserves_order_and_rejects_non_strings(self):
        self.assertEqual(mutations._keys(["serial", "mac"], path=("input", "keys")), ("serial", "mac"))
        self.assertEqual(mutations._keys([], path=("input", "keys")), ())
        with self.assertRaises(mutations._InputError) as context:
            mutations._keys(["serial", 7], path=("input", "keys"))
        self.assertEqual([issue.code for issue in context.exception.issues], ["INVALID_TYPE"])
        self.assertEqual([issue.path for issue in context.exception.issues], [("input", "keys")])

    def test_graphql_path_keeps_declared_paths_and_derives_missing_ones(self):
        cases = (
            (mutations._issue("INVALID_TYPE", path=("input", "choices")), True, ("input", "choices")),
            (mutations._issue("INVALID_TYPE", path=("input", "choices")), False, ("input", "choices")),
            (
                mutations._issue("STALE_RESOURCE", path=("input", "expected_resource_revision")),
                True,
                ("input", "expected_resource_revision"),
            ),
            (mutations._issue("STALE_RESOURCE"), True, ("input", "expectedResourceRevision")),
            (mutations._issue("STALE_DEFINITION"), True, ("input", "expectedDefinitionRevision")),
            (mutations._issue("STALE_PLAN"), True, ("input", "previewToken")),
            (mutations._issue("REFERENCE_CONFLICT"), True, ()),
            (mutations._issue("INVALID_TYPE", path=("set", "hostname")), True, ("input", "patch", "set", "hostname")),
            (
                mutations._issue("INVALID_TYPE", path=("specification_patch", "clear")),
                True,
                ("input", "patch", "clear"),
            ),
        )
        for issue, prefix_input, expected in cases:
            with self.subTest(issue=issue.code, path=issue.path, prefix_input=prefix_input):
                self.assertEqual(
                    mutations._graphql_path(issue, prefix_input=prefix_input),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
