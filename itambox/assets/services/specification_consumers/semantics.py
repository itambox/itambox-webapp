"""Pure presence, status, and comparison semantics for specification consumers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from extras.services.specifications.contracts import SpecificationProjectionDTO

from .contracts import MISSING, FieldFilter, FieldReference, ValueStatus

Presence = Literal["missing", "null", "empty", "value"]


@dataclass(frozen=True)
class ProjectedFieldValue:
    reference: FieldReference
    value: object
    presence: Presence
    status: ValueStatus


def _presence(value: object) -> Presence:
    if value is MISSING:
        return "missing"
    if value is None:
        return "null"
    if value == "" or value == [] or value == ():
        return "empty"
    return "value"


def field_value_from_mapping(
    reference: FieldReference,
    values: Mapping[str, object],
    *,
    status: ValueStatus = "current",
) -> ProjectedFieldValue:
    """Project one source's stored JSON mapping without falling back to another."""

    if status not in {"current", "historical", "invalid", "unknown"}:
        raise ValueError(f"unsupported value status: {status!r}")
    value = values[reference.key] if reference.key in values else MISSING
    return ProjectedFieldValue(reference, value, _presence(value), status)


def field_value_from_projection(
    reference: FieldReference,
    projection: SpecificationProjectionDTO,
) -> ProjectedFieldValue:
    """Project one key from the canonical T10/T11 DTO."""

    for entry in projection.entries:
        if str(entry.key) == reference.key:
            return ProjectedFieldValue(reference, entry.value, _presence(entry.value), entry.state)
    return ProjectedFieldValue(reference, MISSING, "missing", "current")


def _decimal(value: object) -> Decimal | None:
    if type(value) is bool or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _same_value(left: object, right: object, field_type: str | None) -> bool:
    if field_type == "decimal":
        left_decimal = _decimal(left)
        right_decimal = _decimal(right)
        return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal
    if field_type == "date":
        left_date = _date(left)
        right_date = _date(right)
        return left_date is not None and right_date is not None and left_date == right_date
    if field_type == "integer":
        return type(left) is int and type(right) is int and left == right
    if field_type == "boolean":
        return type(left) is bool and type(right) is bool and left is right
    return type(left) is type(right) and left == right


def _ordered_value(value: object, field_type: str | None) -> Decimal | date | str | int | None:
    if field_type in {"integer", "decimal"}:
        return _decimal(value)
    if field_type == "date":
        return _date(value)
    if field_type in {"text", "single_select"} and type(value) is str:
        return value
    return None


def _status_matches(value: ProjectedFieldValue, requested_status: str) -> bool:
    expected = "historical" if requested_status == "history" else requested_status
    return value.status == expected


def matches_filter(
    field: ProjectedFieldValue,
    filter_spec: FieldFilter,
    *,
    field_type: str | None = None,
) -> bool:
    """Evaluate one projected value with strict status and presence semantics."""

    if field.reference != filter_spec.reference or not _status_matches(field, filter_spec.status):
        return False
    operator = filter_spec.operator
    if operator == "is_missing":
        return field.presence == "missing"
    if operator == "is_null":
        return field.presence == "null"
    if operator == "is_empty":
        return field.presence == "empty"
    if field.presence == "missing" or field.presence == "null":
        return False

    expected = filter_spec.value
    if operator == "eq":
        return _same_value(field.value, expected, field_type)
    if operator == "neq":
        return not _same_value(field.value, expected, field_type)
    if operator == "contains":
        return type(field.value) is str and type(expected) is str and expected.casefold() in field.value.casefold()
    if operator in {"contains_any", "contains_all"}:
        if not isinstance(field.value, (list, tuple)) or not isinstance(expected, (list, tuple, set, frozenset)):
            return False
        actual = set(field.value)
        wanted = set(expected)
        return bool(actual.intersection(wanted)) if operator == "contains_any" else wanted.issubset(actual)

    actual_ordered = _ordered_value(field.value, field_type)
    expected_ordered = _ordered_value(expected, field_type)
    if actual_ordered is None or expected_ordered is None:
        return False
    if operator == "gt":
        return actual_ordered > expected_ordered
    if operator == "gte":
        return actual_ordered >= expected_ordered
    if operator == "lt":
        return actual_ordered < expected_ordered
    if operator == "lte":
        return actual_ordered <= expected_ordered
    return False


def project_source_values(
    reference_values: Mapping[FieldReference, Mapping[str, object]],
    references: Sequence[FieldReference],
) -> tuple[ProjectedFieldValue, ...]:
    """Project references from their explicitly selected source maps."""

    projected: list[ProjectedFieldValue] = []
    for reference in references:
        projected.append(field_value_from_mapping(reference, reference_values.get(reference, {})))
    return tuple(projected)
