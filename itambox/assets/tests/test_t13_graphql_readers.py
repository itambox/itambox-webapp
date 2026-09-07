from __future__ import annotations

from dataclasses import dataclass

import graphene
import pytest
from graphql import GraphQLError
from graphql.language import ast

from assets.graphql_specifications.loaders import RequestScopedSpecificationLoader
from assets.graphql_specifications.scalars import DecimalScalar, SafeInteger
from assets.graphql_specifications.types import SpecificationDefinitionType, SpecificationEntryType
from extras.services.specifications.contracts import (
    ChoiceDTO,
    ChoiceSetDTO,
    DefinitionRevision,
    FieldDefinitionDTO,
    FieldKey,
    LoadedSpecificationGraphDTO,
    ProjectionIssueDTO,
    ResolvedFieldDTO,
    ResolvedSectionDTO,
    SpecificationDefinitionDTO,
    SpecificationProjectionDTO,
    SpecificationProjectionEntryDTO,
    SpecificationValidationDTO,
    StoredSpecificationEntryDTO,
)


def _field(*, field_type: str = "integer", key: str = "memory_capacity") -> FieldDefinitionDTO:
    choice_set = None
    if field_type == "single_select":
        choice_set = ChoiceSetDTO(
            identity="core/storage-medium",
            label="Storage medium",
            resource_revision="sha256:choice-set",
            lifecycle="active",
            choices=(
                ChoiceDTO(key="ssd", label="SSD", lifecycle="active", position=1),
                ChoiceDTO(key="retired", label="Retired", lifecycle="deprecated", position=2),
            ),
        )
    return FieldDefinitionDTO(
        resource_revision="sha256:field",
        key=FieldKey(key),
        identity=f"core/{key}",
        label="Memory capacity",
        help_text="",
        targets=frozenset({"asset_type", "asset"}),
        activation="composed",
        field_type=field_type,
        quantity_kind="capacity" if field_type == "decimal" else None,
        canonical_unit="GiB" if field_type == "decimal" else None,
        validation=SpecificationValidationDTO(
            minimum=None,
            maximum=None,
            scale=3 if field_type == "decimal" else None,
            max_length=None,
            max_values=None,
            regex=None,
            rule=None,
        ),
        required=False,
        nullable=True,
        lifecycle="active",
        choice_set=choice_set,
    )


def _definition() -> SpecificationDefinitionDTO:
    field = _field(field_type="decimal")
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
        first_placement_section_identity="core/compute",
        contributing_section_identities=("core/compute",),
    )
    section = ResolvedSectionDTO(
        section_kind="persisted_fieldset",
        identity="core/compute",
        label="Compute",
        description="",
        persisted_ordinal=1,
        fields=(resolved,),
    )
    return SpecificationDefinitionDTO(
        revision=DefinitionRevision("definition-revision"),
        target_kind="asset_type",
        persisted_memberships=(),
        rendered_sections=(section,),
    )


def test_safe_integer_uses_javascript_safe_range_and_accepts_beyond_graphql_int() -> None:
    assert SafeInteger.serialize(2_147_483_648) == 2_147_483_648
    assert SafeInteger.parse_value(9_007_199_254_740_991) == 9_007_199_254_740_991
    assert SafeInteger.parse_literal(ast.IntValueNode(value="9007199254740991")) == 9_007_199_254_740_991

    with pytest.raises((GraphQLError, ValueError, TypeError)):
        SafeInteger.parse_value(9_007_199_254_740_992)
    with pytest.raises((GraphQLError, ValueError, TypeError)):
        SafeInteger.parse_value(True)


def test_decimal_scalar_is_string_valued_and_preserves_fixed_scale_text() -> None:
    assert DecimalScalar.serialize("24.000") == "24.000"
    assert DecimalScalar.parse_value("24.000") == "24.000"
    assert DecimalScalar.parse_literal(ast.StringValueNode(value="24.000")) == "24.000"

    with pytest.raises((GraphQLError, ValueError, TypeError)):
        DecimalScalar.parse_value(24.0)
    with pytest.raises((GraphQLError, ValueError, TypeError)):
        DecimalScalar.parse_value(DecimalScalar)


def test_typed_definition_and_union_execute_with_decimal_and_history_escape_hatch() -> None:
    definition = _definition()
    projection = SpecificationProjectionDTO(
        entries=(
            SpecificationProjectionEntryDTO(
                key=FieldKey("memory_capacity"),
                value="24.000",
                state="current",
                reason_codes=("ACTIVE_VALUE",),
                definition=_field(field_type="decimal"),
            ),
            SpecificationProjectionEntryDTO(
                key=FieldKey("removed_field"),
                value={"unexpected": ["history"]},
                state="unknown",
                reason_codes=("UNKNOWN_DEFINITION",),
                definition=None,
            ),
        ),
        missing_required_issues=(ProjectionIssueDTO("MISSING_REQUIRED", FieldKey("required_field")),),
    )

    class Query(graphene.ObjectType):
        definition = graphene.Field(SpecificationDefinitionType)
        entries = graphene.List(SpecificationEntryType)

        def resolve_definition(self, info):
            return definition

        def resolve_entries(self, info):
            return projection.entries

    result = graphene.Schema(query=Query).execute(
        """
        {
          definition {
            revision
            target
            sections { identity fields { key fieldType canonicalUnit sources } }
          }
          entries {
            key state reasonCodes
            value {
              __typename
              ... on DecimalSpecificationValue { decimal }
              ... on UninterpretedSpecificationValue { json }
            }
          }
        }
        """
    )
    assert result.errors is None, result.errors
    assert result.data == {
        "definition": {
            "revision": "definition-revision",
            "target": "ASSET_TYPE",
            "sections": [
                {
                    "identity": "core/compute",
                    "fields": [
                        {
                            "key": "memory_capacity",
                            "fieldType": "DECIMAL",
                            "canonicalUnit": "GiB",
                            "sources": ["core/compute"],
                        }
                    ],
                }
            ],
        },
        "entries": [
            {
                "key": "memory_capacity",
                "state": "CURRENT",
                "reasonCodes": ["ACTIVE_VALUE"],
                "value": {"__typename": "DecimalSpecificationValue", "decimal": "24.000"},
            },
            {
                "key": "removed_field",
                "state": "UNKNOWN",
                "reasonCodes": ["UNKNOWN_DEFINITION"],
                "value": {"__typename": "UninterpretedSpecificationValue", "json": {"unexpected": ["history"]}},
            },
        ],
    }


def test_request_loader_batches_distinct_type_ids_and_does_not_cross_request_cache() -> None:
    calls: list[tuple[int, ...]] = []
    graph = LoadedSpecificationGraphDTO(
        type_memberships={},
        fieldsets_by_identity={},
        fields_by_key={},
        global_field_keys_by_target={},
        historical_definitions_by_key={},
    )

    def graph_loader(request):
        calls.append(tuple(request.asset_type_ids))
        return graph

    loader = RequestScopedSpecificationLoader(graph_loader=graph_loader)
    loader.prepare_type_ids((7, 7, 8), target_kind="asset_type")
    assert calls == [(7, 8)]
    assert loader.graph_for_type(7, target_kind="asset_type") is graph
    assert loader.graph_for_type(8, target_kind="asset_type") is graph
    assert calls == [(7, 8)]

    second_loader = RequestScopedSpecificationLoader(graph_loader=graph_loader)
    second_loader.prepare_type_ids((7,), target_kind="asset_type")
    assert calls == [(7, 8), (7,)]


def test_request_loader_rejects_missing_context_instead_of_using_ambient_visibility() -> None:
    from assets.graphql_specifications.loaders import request_loader_for_info

    @dataclass
    class Info:
        context: object

    with pytest.raises(GraphQLError):
        request_loader_for_info(Info(context=None))


def test_asset_schema_executes_typed_reader_contract_without_static_only_shortcut() -> None:
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")
    os.environ.setdefault("ITAMBOX_ENV", "dev")
    import django

    django.setup()
    from assets.schema import Query

    schema = graphene.Schema(query=Query)
    sdl = str(schema)
    assert "assetTypes(first: Int! = 50, after: Cursor): AssetTypeConnection!" in sdl
    assert "specificationFields(first: Int! = 50, after: Cursor): SpecificationFieldConnection!" in sdl
    assert "integer: SafeInteger!" in sdl
    assert "decimal: Decimal!" in sdl
    result = schema.execute("{ __typename }")
    assert result.errors is None
    assert result.data == {"__typename": "Query"}
