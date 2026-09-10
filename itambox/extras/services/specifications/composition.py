"""Pure, value-independent composition of specification definitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, NoReturn

from .contracts import (
    ChoiceSetDTO,
    DefinitionRevision,
    FieldDefinitionDTO,
    FieldKey,
    LoadedSpecificationGraphDTO,
    OrderedFieldMembershipDTO,
    OrderedFieldsetMembershipDTO,
    PersistedFieldsetDTO,
    QualifiedIdentity,
    ResolvedFieldDTO,
    ResolvedSectionDTO,
    SpecificationDefinitionDTO,
)

if TYPE_CHECKING:
    from assets.services.specifications.contracts import SpecificationResolutionRequest


class SpecificationDefinitionError(ValueError):
    """Raised when a loaded composition graph is internally inconsistent."""


def _invalid_definition(message: str) -> NoReturn:
    raise SpecificationDefinitionError(message)


def _normalize_section_memberships(
    memberships: Sequence[OrderedFieldsetMembershipDTO],
) -> tuple[OrderedFieldsetMembershipDTO, ...]:
    seen_identities: set[str] = set()
    seen_ordinals: set[int] = set()
    ordered = tuple(memberships)
    for membership in ordered:
        identity = str(membership.fieldset_identity)
        if identity in seen_identities:
            _invalid_definition(f"duplicate Fieldset membership: {identity}")
        if type(membership.ordinal) is not int or membership.ordinal < 1:
            _invalid_definition(f"invalid Fieldset ordinal for {identity}: {membership.ordinal!r}")
        if membership.ordinal in seen_ordinals:
            _invalid_definition(f"duplicate Fieldset ordinal: {membership.ordinal}")
        seen_identities.add(identity)
        seen_ordinals.add(membership.ordinal)

    return tuple(
        OrderedFieldsetMembershipDTO(
            fieldset_identity=membership.fieldset_identity,
            ordinal=ordinal,
        )
        for ordinal, membership in enumerate(
            sorted(ordered, key=lambda item: (item.ordinal, str(item.fieldset_identity))),
            start=1,
        )
    )


def _normalize_field_memberships(
    fieldset: PersistedFieldsetDTO,
) -> tuple[OrderedFieldMembershipDTO, ...]:
    seen_identities: set[str] = set()
    seen_ordinals: set[int] = set()
    ordered = tuple(fieldset.field_memberships)
    for membership in ordered:
        identity = str(membership.field_identity)
        if identity in seen_identities:
            _invalid_definition(f"duplicate Field membership in {fieldset.identity}: {identity}")
        if type(membership.ordinal) is not int or membership.ordinal < 1:
            _invalid_definition(f"invalid Field ordinal for {fieldset.identity}/{identity}")
        if membership.ordinal in seen_ordinals:
            _invalid_definition(f"duplicate Field ordinal in {fieldset.identity}: {membership.ordinal}")
        seen_identities.add(identity)
        seen_ordinals.add(membership.ordinal)
    return tuple(
        OrderedFieldMembershipDTO(field_identity=membership.field_identity, ordinal=ordinal)
        for ordinal, membership in enumerate(
            sorted(ordered, key=lambda item: (item.ordinal, str(item.field_identity))),
            start=1,
        )
    )


def _fields_by_identity(graph: LoadedSpecificationGraphDTO) -> dict[str, FieldDefinitionDTO]:
    result: dict[str, FieldDefinitionDTO] = {}
    for field in graph.fields_by_key.values():
        identity = str(field.identity)
        if identity in result and result[identity] != field:
            _invalid_definition(f"conflicting Field identity: {identity}")
        result[identity] = field
    return result


def _field_for_membership(
    fields_by_identity: Mapping[str, FieldDefinitionDTO],
    field_identity: str,
) -> FieldDefinitionDTO:
    field = fields_by_identity.get(field_identity)
    if field is None:
        _invalid_definition(f"unresolved Field membership: {field_identity}")
    return field


def _validate_field_activation(field: FieldDefinitionDTO, fieldset_identity: str) -> None:
    if field.activation == "global":
        _invalid_definition(f"global Field cannot be a member of Fieldset: {fieldset_identity}/{field.key}")
    if field.activation != "composed":
        _invalid_definition(f"unsupported Field activation: {field.activation!r}")
    if field.lifecycle not in {"active", "deprecated"}:
        _invalid_definition(f"unsupported Field lifecycle: {field.lifecycle!r}")


def _validate_fieldset_graph(
    memberships: Sequence[OrderedFieldsetMembershipDTO],
    graph: LoadedSpecificationGraphDTO,
    fields_by_identity: Mapping[str, FieldDefinitionDTO],
) -> dict[str, tuple[OrderedFieldMembershipDTO, ...]]:
    field_memberships_by_identity: dict[str, tuple[OrderedFieldMembershipDTO, ...]] = {}
    for membership in memberships:
        identity = str(membership.fieldset_identity)
        fieldset = graph.fieldsets_by_identity.get(membership.fieldset_identity)
        if fieldset is None:
            _invalid_definition(f"unresolved Fieldset membership: {identity}")
        if fieldset.lifecycle not in {"active", "deprecated"}:
            _invalid_definition(f"unsupported Fieldset lifecycle: {fieldset.lifecycle!r}")
        normalized_fields = _normalize_field_memberships(fieldset)
        for field_membership in normalized_fields:
            field = _field_for_membership(fields_by_identity, str(field_membership.field_identity))
            _validate_field_activation(field, identity)
        field_memberships_by_identity[identity] = normalized_fields
    return field_memberships_by_identity


def _resolved_field(
    field: FieldDefinitionDTO,
    section_identity: str | None,
) -> ResolvedFieldDTO:
    return ResolvedFieldDTO(
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
        first_placement_section_identity=(
            QualifiedIdentity(section_identity) if section_identity is not None else None
        ),
        contributing_section_identities=(
            (QualifiedIdentity(section_identity),) if section_identity is not None else ()
        ),
    )


def _is_applicable(field: FieldDefinitionDTO, target_kind: str) -> bool:
    return field.lifecycle == "active" and target_kind in field.targets


def _resolve_persisted_sections(
    memberships: Sequence[OrderedFieldsetMembershipDTO],
    graph: LoadedSpecificationGraphDTO,
    target_kind: str,
    fields_by_identity: Mapping[str, FieldDefinitionDTO],
    field_memberships_by_identity: Mapping[str, tuple[OrderedFieldMembershipDTO, ...]],
) -> tuple[ResolvedSectionDTO, ...]:
    field_states: dict[FieldKey, ResolvedFieldDTO] = {}
    first_keys_by_section: list[tuple[PersistedFieldsetDTO, int, tuple[FieldKey, ...]]] = []

    for membership in memberships:
        fieldset = graph.fieldsets_by_identity[membership.fieldset_identity]
        if fieldset.lifecycle == "deprecated":
            continue
        first_keys: list[FieldKey] = []
        for field_membership in field_memberships_by_identity[str(fieldset.identity)]:
            field = _field_for_membership(fields_by_identity, str(field_membership.field_identity))
            if not _is_applicable(field, target_kind):
                continue
            current = field_states.get(field.key)
            section_identity = str(fieldset.identity)
            if current is None:
                field_states[field.key] = _resolved_field(field, section_identity)
                first_keys.append(field.key)
            else:
                field_states[field.key] = replace(
                    current,
                    contributing_section_identities=(
                        *current.contributing_section_identities,
                        QualifiedIdentity(section_identity),
                    ),
                )
        first_keys_by_section.append((fieldset, membership.ordinal, tuple(first_keys)))

    sections = tuple(
        ResolvedSectionDTO(
            section_kind="persisted_fieldset",
            identity=fieldset.identity,
            label=fieldset.label,
            description=fieldset.description,
            persisted_ordinal=ordinal,
            fields=tuple(field_states[key] for key in first_keys),
        )
        for fieldset, ordinal, first_keys in first_keys_by_section
        if first_keys
    )
    return sections


def _global_field_keys(
    graph: LoadedSpecificationGraphDTO,
    target_kind: str,
) -> tuple[FieldKey, ...]:
    indexed_keys = tuple(graph.global_field_keys_by_target.get(target_kind, ()))
    explicit_keys = tuple(
        field.key
        for field in graph.fields_by_key.values()
        if field.activation == "global" and target_kind in field.targets
    )
    keys: dict[str, FieldKey] = {}
    for key in (*indexed_keys, *explicit_keys):
        keys[str(key)] = FieldKey(str(key))
    return tuple(keys.values())


def _resolve_global_fields(
    graph: LoadedSpecificationGraphDTO,
    target_kind: str,
) -> tuple[ResolvedFieldDTO, ...]:
    fields: list[FieldDefinitionDTO] = []
    for key in _global_field_keys(graph, target_kind):
        field = graph.fields_by_key.get(key)
        if field is None:
            _invalid_definition(f"unresolved global Field: {key}")
        if field.activation != "global":
            _invalid_definition(f"global index names non-global Field: {key}")
        if field.lifecycle == "active" and target_kind in field.targets:
            fields.append(field)
        elif field.lifecycle not in {"active", "deprecated"}:
            _invalid_definition(f"unsupported Field lifecycle: {field.lifecycle!r}")

    return tuple(_resolved_field(field, None) for field in sorted(fields, key=lambda item: (item.label, str(item.key))))


def _choice_set_payload(choice_set: ChoiceSetDTO | None) -> dict | None:
    if choice_set is None:
        return None
    return {
        "identity": str(choice_set.identity),
        "label": choice_set.label,
        "resource_revision": str(choice_set.resource_revision),
        "lifecycle": choice_set.lifecycle,
        "choices": [
            {
                "key": choice.key,
                "label": choice.label,
                "lifecycle": choice.lifecycle,
                "position": choice.position,
            }
            for choice in choice_set.choices
        ],
    }


def _validation_payload(field: FieldDefinitionDTO | ResolvedFieldDTO) -> dict:
    validation = field.validation
    return {
        "minimum": str(validation.minimum) if validation.minimum is not None else None,
        "maximum": str(validation.maximum) if validation.maximum is not None else None,
        "scale": validation.scale,
        "max_length": validation.max_length,
        "max_values": validation.max_values,
        "regex": validation.regex,
        "rule": validation.rule,
    }


def _field_payload(field: FieldDefinitionDTO | ResolvedFieldDTO) -> dict:
    payload = {
        "resource_revision": str(field.resource_revision),
        "key": str(field.key),
        "identity": str(field.identity),
        "label": field.label,
        "help_text": field.help_text,
        "targets": sorted(str(target) for target in field.targets),
        "activation": field.activation,
        "field_type": field.field_type,
        "quantity_kind": field.quantity_kind,
        "canonical_unit": field.canonical_unit,
        "validation": _validation_payload(field),
        "required": field.required,
        "nullable": field.nullable,
        "lifecycle": field.lifecycle,
        "choice_set": _choice_set_payload(field.choice_set),
    }
    if isinstance(field, ResolvedFieldDTO):
        payload["first_placement_section_identity"] = (
            str(field.first_placement_section_identity) if field.first_placement_section_identity is not None else None
        )
        payload["contributing_section_identities"] = [
            str(identity) for identity in field.contributing_section_identities
        ]
    return payload


def _fieldset_payload(fieldset: PersistedFieldsetDTO) -> dict:
    return {
        "identity": str(fieldset.identity),
        "label": fieldset.label,
        "description": fieldset.description,
        "resource_revision": str(fieldset.resource_revision),
        "lifecycle": fieldset.lifecycle,
        "field_memberships": [
            {"field_identity": str(item.field_identity), "ordinal": item.ordinal}
            for item in _normalize_field_memberships(fieldset)
        ],
    }


def _revision_payload(
    target_kind: str,
    memberships: Sequence[OrderedFieldsetMembershipDTO],
    graph: LoadedSpecificationGraphDTO,
    rendered_sections: Sequence[ResolvedSectionDTO],
) -> dict:
    return {
        "target_kind": target_kind,
        "persisted_memberships": [
            {
                "fieldset": _fieldset_payload(graph.fieldsets_by_identity[item.fieldset_identity]),
                "ordinal": item.ordinal,
            }
            for item in memberships
        ],
        "rendered_sections": [
            {
                "section_kind": section.section_kind,
                "identity": str(section.identity) if section.identity is not None else None,
                "label": section.label,
                "description": section.description,
                "persisted_ordinal": section.persisted_ordinal,
                "fields": [_field_payload(field) for field in section.fields],
            }
            for section in rendered_sections
        ],
    }


def _definition_revision(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_specification_definition(
    request: SpecificationResolutionRequest,
) -> SpecificationDefinitionDTO:
    """Resolve an effective definition from a loaded graph without reading values.

    The request is the canonical DTO owned by ``assets.services.specifications``.
    The annotation is available to type checkers, while the runtime module remains
    independent of Assets and Django.
    """
    memberships = _normalize_section_memberships(request.ordered_memberships)
    graph = request.loaded_graph
    fields_by_identity = _fields_by_identity(graph)
    field_memberships_by_identity = _validate_fieldset_graph(memberships, graph, fields_by_identity)
    persisted_sections = _resolve_persisted_sections(
        memberships,
        graph,
        request.target_kind,
        fields_by_identity,
        field_memberships_by_identity,
    )
    global_fields = _resolve_global_fields(graph, request.target_kind)
    rendered_sections = persisted_sections
    if global_fields:
        rendered_sections += (
            ResolvedSectionDTO(
                section_kind="derived_additional",
                identity=None,
                label="Additional specifications",
                description="",
                persisted_ordinal=None,
                fields=global_fields,
            ),
        )

    revision = _definition_revision(_revision_payload(request.target_kind, memberships, graph, rendered_sections))
    return SpecificationDefinitionDTO(
        revision=DefinitionRevision(revision),
        target_kind=request.target_kind,
        persisted_memberships=memberships,
        rendered_sections=rendered_sections,
    )
