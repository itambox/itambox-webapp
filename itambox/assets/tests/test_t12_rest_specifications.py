"""Request-level and transport-contract coverage for the final REST surface."""

from __future__ import annotations

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory

from assets.api.specification_api import (
    ApplyCategoryDefaultsInputSerializer,
    CompositionInputSerializer,
    HistoryCleanupInputSerializer,
    SpecificationPatchInputSerializer,
    command_result_response,
    definition_payload,
    if_match_revision,
    projection_payload,
)
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    DefinitionRevision,
    DomainIssueDTO,
    FieldKey,
    OwnerChangedDTO,
    OwnerRefDTO,
    ResourceRevision,
    SpecificationDefinitionDTO,
)
from extras.services.specifications.contracts import (
    ChoiceDTO,
    ChoiceSetDTO,
    FieldDefinitionDTO,
    OrderedFieldsetMembershipDTO,
    ResolvedFieldDTO,
    ResolvedSectionDTO,
    SpecificationProjectionDTO,
    SpecificationValidationDTO,
)


class SpecificationTransportSerializerTests(SimpleTestCase):
    def test_patch_rejects_raw_replacement_and_unknown_operations(self):
        serializer = SpecificationPatchInputSerializer(
            data={"set": {}, "clear": [], "custom_field_data": {"secret": "value"}}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("custom_field_data", serializer.errors)

        serializer = SpecificationPatchInputSerializer(data={"replace": {"field": "value"}})
        self.assertFalse(serializer.is_valid())
        self.assertIn("replace", serializer.errors)

    def test_composition_requires_ordered_fieldsets_and_rejects_singular_wire_name(self):
        serializer = CompositionInputSerializer(data={"custom_fieldset": "local/legacy"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("custom_fieldset", serializer.errors)

        serializer = CompositionInputSerializer(data={"fieldsets": None})
        self.assertFalse(serializer.is_valid())
        self.assertIn("fieldsets", serializer.errors)

    def test_history_and_apply_inputs_reject_unknown_ids_and_raw_values(self):
        history = HistoryCleanupInputSerializer(data={"keys": ["old"], "asset_id": 999})
        self.assertFalse(history.is_valid())
        self.assertIn("asset_id", history.errors)

        apply = ApplyCategoryDefaultsInputSerializer(
            data={"preview_token": "token", "custom_field_data": {"old": "value"}}
        )
        self.assertFalse(apply.is_valid())
        self.assertIn("custom_field_data", apply.errors)

    def test_if_match_preserves_opaque_revision_inside_http_etag(self):
        factory = APIRequestFactory()
        request = factory.patch("/api/assets/asset-types/7/", HTTP_IF_MATCH='"sha256:abc"')
        self.assertEqual(if_match_revision(request), "sha256:abc")

        weak = factory.patch("/api/assets/asset-types/7/", HTTP_IF_MATCH='W/"sha256:def"')
        self.assertEqual(if_match_revision(weak), "sha256:def")

    def test_missing_and_stale_domain_results_use_final_status_and_transport_paths(self):
        missing = CommandRejectedDTO(
            outcome="rejected",
            safe_owner=None,
            issues=(
                DomainIssueDTO(
                    code="MISSING_PRECONDITION",
                    path=("preview_token",),
                    field_key=None,
                    message_key="specifications.missing_precondition",
                ),
            ),
        )
        response = command_result_response(missing)
        self.assertEqual(response.status_code, status.HTTP_428_PRECONDITION_REQUIRED)
        self.assertEqual(response.data["error"]["code"], "MISSING_PRECONDITION")
        self.assertEqual(response.data["error"]["issues"][0]["path"], ["preview_token"])

        stale = CommandRejectedDTO(
            outcome="rejected",
            safe_owner=OwnerRefDTO("asset_type", 7),
            issues=(
                DomainIssueDTO(
                    code="STALE_DEFINITION",
                    path=(),
                    field_key=None,
                    message_key="specifications.stale_definition",
                ),
            ),
        )
        response = command_result_response(stale)
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(
            response.data["error"]["issues"][0]["path"],
            ["expected_definition_revision"],
        )


class SpecificationPayloadTests(SimpleTestCase):
    def setUp(self):
        validation = SpecificationValidationDTO(
            minimum=None,
            maximum=None,
            scale=3,
            max_length=None,
            max_values=None,
            regex=None,
            rule=None,
        )
        choice_set = ChoiceSetDTO(
            identity="local/state",
            label="State",
            resource_revision="sha256:choice",
            lifecycle="active",
            choices=(ChoiceDTO("on", "On", "active", 1),),
        )
        field = FieldDefinitionDTO(
            resource_revision="sha256:field",
            key=FieldKey("state"),
            identity="local/state",
            label="State",
            help_text="",
            targets=frozenset({"asset_type"}),
            activation="composed",
            field_type="single_select",
            quantity_kind=None,
            canonical_unit=None,
            validation=validation,
            required=False,
            nullable=True,
            lifecycle="active",
            choice_set=choice_set,
        )
        resolved = ResolvedFieldDTO(
            resource_revision=field.resource_revision,
            key=field.key,
            identity=field.identity,
            label=field.label,
            help_text=field.help_text,
            targets=field.targets,
            activation=field.activation,
            field_type=field.field_type,
            quantity_kind=field.quantity_kind,
            canonical_unit=field.canonical_unit,
            validation=field.validation,
            required=field.required,
            nullable=field.nullable,
            lifecycle=field.lifecycle,
            choice_set=field.choice_set,
            first_placement_section_identity="local/first",
            contributing_section_identities=("local/first", "local/second"),
        )
        self.definition = SpecificationDefinitionDTO(
            revision=DefinitionRevision("sha256:def"),
            target_kind="asset_type",
            persisted_memberships=(
                OrderedFieldsetMembershipDTO("local/second", 1),
                OrderedFieldsetMembershipDTO("local/first", 2),
            ),
            rendered_sections=(
                ResolvedSectionDTO(
                    section_kind="persisted_fieldset",
                    identity="local/second",
                    label="Second",
                    description="",
                    persisted_ordinal=1,
                    fields=(resolved,),
                ),
            ),
        )
        self.projection = SpecificationProjectionDTO(entries=(), missing_required_issues=())

    def test_definition_preserves_order_and_exposes_metadata_as_lists(self):
        payload = definition_payload(self.definition)
        self.assertEqual(
            [item["identity"] for item in payload["fieldsets"]],
            ["local/second", "local/first"],
        )
        self.assertEqual(payload["sections"][0]["fields"][0]["contributing_sections"], ["local/first", "local/second"])
        self.assertEqual(payload["sections"][0]["fields"][0]["choice_set"]["choices"][0]["key"], "on")

    def test_projection_payload_has_one_value_map_and_history_status(self):
        payload = projection_payload(self.projection)
        self.assertEqual(
            payload,
            {"specifications": {}, "specification_state": {"complete": True, "issues": [], "historical_keys": []}},
        )


class SpecificationMutationPayloadTests(SimpleTestCase):
    def test_success_result_identifies_owner_and_revisions(self):
        result = OwnerChangedDTO(
            outcome="changed",
            owner=OwnerRefDTO("asset_type", 7),
            resource_revision=ResourceRevision("sha256:resource"),
            definition_revision=DefinitionRevision("sha256:definition"),
        )
        response = command_result_response(result)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["owner"], {"kind": "asset_type", "id": 7})
        self.assertEqual(response.data["resource_revision"], "sha256:resource")
