"""Pure read-side projection of stored specification values.

Definition resolution is deliberately outside this module. The projection consumes an
already resolved, value-independent definition and immutable stored entries, preserving
their raw values while attaching current/history/invalid/unknown diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from .codecs import SpecificationCodecError, normalize_specification_value, validate_required_fields
from .contracts import (
    FieldDefinitionDTO,
    FieldKey,
    JSONValue,
    ProjectionIssueDTO,
    ProjectionReasonCode,
    ProjectionState,
    ResolvedFieldDTO,
    SpecificationDefinitionDTO,
    SpecificationProjectionDTO,
    SpecificationProjectionEntryDTO,
)

if TYPE_CHECKING:
    from assets.services.specifications.contracts import SpecificationProjectionRequest


_REASON_ORDER: tuple[ProjectionReasonCode, ...] = (
    "ACTIVE_VALUE",
    "INACTIVE_COMPOSITION",
    "DEPRECATED_FIELD",
    "DEPRECATED_CHOICE",
    "INVALID_STORED_VALUE",
    "UNKNOWN_DEFINITION",
    "MISSING_REQUIRED",
)
_HISTORICAL_REASONS = frozenset({"INACTIVE_COMPOSITION", "DEPRECATED_FIELD", "DEPRECATED_CHOICE"})


def _field_definition(resolved_field: ResolvedFieldDTO) -> FieldDefinitionDTO:
    """Strip placement-only metadata before attaching a definition to a value."""
    return FieldDefinitionDTO(
        resource_revision=resolved_field.resource_revision,
        key=resolved_field.key,
        identity=resolved_field.identity,
        label=resolved_field.label,
        help_text=resolved_field.help_text,
        targets=resolved_field.targets,
        activation=resolved_field.activation,
        field_type=resolved_field.field_type,
        quantity_kind=resolved_field.quantity_kind,
        canonical_unit=resolved_field.canonical_unit,
        validation=resolved_field.validation,
        required=resolved_field.required,
        nullable=resolved_field.nullable,
        lifecycle=resolved_field.lifecycle,
        choice_set=resolved_field.choice_set,
    )


def _current_definitions(definition: SpecificationDefinitionDTO) -> dict[FieldKey, FieldDefinitionDTO]:
    """Index rendered fields once; repeated placements share the first definition."""
    current: dict[FieldKey, FieldDefinitionDTO] = {}
    for section in definition.rendered_sections:
        for resolved_field in section.fields:
            current.setdefault(resolved_field.key, _field_definition(resolved_field))
    return current


def _deprecated_choice_keys(definition: FieldDefinitionDTO) -> frozenset[str]:
    choice_set = definition.choice_set
    if choice_set is None:
        return frozenset()
    return frozenset(choice.key for choice in choice_set.choices if choice.lifecycle == "deprecated")


def _contains_deprecated_choice(definition: FieldDefinitionDTO, value: JSONValue) -> bool:
    deprecated_keys = _deprecated_choice_keys(definition)
    if not deprecated_keys:
        return False
    if type(value) is str:
        return value in deprecated_keys
    if type(value) in {list, tuple}:
        return any(type(item) is str and item in deprecated_keys for item in value)
    return False


def _validation_definition(definition: FieldDefinitionDTO) -> FieldDefinitionDTO:
    """Validate retired values without making the retired Field writable."""
    if definition.lifecycle == "deprecated":
        return replace(definition, lifecycle="active")
    return definition


def _is_valid_stored_value(definition: FieldDefinitionDTO, value: JSONValue, key: FieldKey) -> bool:
    try:
        normalize_specification_value(
            _validation_definition(definition),
            value,
            original_value=value,
            path=("stored", str(key)),
        )
    except SpecificationCodecError:
        return False
    return True


def _ordered_reasons(reasons: set[ProjectionReasonCode]) -> tuple[ProjectionReasonCode, ...]:
    return tuple(reason for reason in _REASON_ORDER if reason in reasons)


def _classify_known_entry(
    definition: FieldDefinitionDTO,
    value: JSONValue,
    *,
    is_current: bool,
) -> tuple[ProjectionState, tuple[ProjectionReasonCode, ...]]:
    reasons: set[ProjectionReasonCode] = set()
    if definition.lifecycle == "deprecated":
        reasons.add("DEPRECATED_FIELD")
    elif not is_current:
        reasons.add("INACTIVE_COMPOSITION")
    if _contains_deprecated_choice(definition, value):
        reasons.add("DEPRECATED_CHOICE")
    is_valid = _is_valid_stored_value(definition, value, definition.key)
    if not is_valid:
        reasons.add("INVALID_STORED_VALUE")
    elif not reasons and is_current:
        reasons.add("ACTIVE_VALUE")

    ordered_reasons = _ordered_reasons(reasons)
    if "INVALID_STORED_VALUE" in reasons:
        state: ProjectionState = "invalid"
    elif reasons.intersection(_HISTORICAL_REASONS):
        state = "historical"
    else:
        state = "current"
    return state, ordered_reasons


def _missing_required_issues(
    current_definitions: Mapping[FieldKey, FieldDefinitionDTO],
    stored_values: Mapping[FieldKey, JSONValue],
) -> tuple[ProjectionIssueDTO, ...]:
    try:
        validate_required_fields(current_definitions, stored_values)
    except SpecificationCodecError as error:
        field_keys = sorted(
            {issue.field_key for issue in error.issues if issue.field_key is not None},
            key=str,
        )
        return tuple(ProjectionIssueDTO("MISSING_REQUIRED", field_key) for field_key in field_keys)
    return ()


def project_specification_values(request: SpecificationProjectionRequest) -> SpecificationProjectionDTO:
    """Project raw stored entries against a resolved definition without changing them.

    The returned entry value is always the exact value from the request. Codec output is
    intentionally discarded: it is used only to identify invalid stored state. Missing
    required fields are reported separately because an absent value has no entry to
    project. Unknown stored keys are retained as explicit ``unknown`` entries.
    """
    current_definitions = _current_definitions(request.definition)
    stored_values = {entry.key: entry.value for entry in request.stored_entries}
    missing_required = _missing_required_issues(current_definitions, stored_values)

    entries: list[SpecificationProjectionEntryDTO] = []
    for stored_entry in request.stored_entries:
        current_definition = current_definitions.get(stored_entry.key)
        if current_definition is not None:
            state, reason_codes = _classify_known_entry(
                current_definition,
                stored_entry.value,
                is_current=True,
            )
            entry_definition = current_definition
        else:
            historical_definition = request.historical_definitions_by_key.get(stored_entry.key)
            if historical_definition is None:
                entries.append(
                    SpecificationProjectionEntryDTO(
                        key=stored_entry.key,
                        value=stored_entry.value,
                        state="unknown",
                        reason_codes=("UNKNOWN_DEFINITION",),
                        definition=None,
                    )
                )
                continue
            state, reason_codes = _classify_known_entry(
                historical_definition,
                stored_entry.value,
                is_current=False,
            )
            entry_definition = historical_definition
        entries.append(
            SpecificationProjectionEntryDTO(
                key=stored_entry.key,
                value=stored_entry.value,
                state=state,
                reason_codes=reason_codes,
                definition=entry_definition,
            )
        )

    return SpecificationProjectionDTO(
        entries=tuple(entries),
        missing_required_issues=missing_required,
    )
