"""Pure conversion from specification DTOs to GraphQL-facing read values."""

from __future__ import annotations

from dataclasses import dataclass

from extras.services.specifications.contracts import (
    FieldDefinitionDTO,
    LoadedSpecificationGraphDTO,
    PersistedFieldsetDTO,
    ProjectionIssueDTO,
    ResolvedFieldDTO,
    SpecificationProjectionEntryDTO,
)


@dataclass(frozen=True)
class UserErrorView:
    code: str
    path: tuple[str, ...]
    field_key: str | None
    message: str


@dataclass(frozen=True)
class TextSpecificationValue:
    text: str


@dataclass(frozen=True)
class IntegerSpecificationValue:
    integer: int


@dataclass(frozen=True)
class DecimalSpecificationValue:
    decimal: str


@dataclass(frozen=True)
class BooleanSpecificationValue:
    boolean: bool


@dataclass(frozen=True)
class DateSpecificationValue:
    date: str


@dataclass(frozen=True)
class ChoiceSpecificationValue:
    choice: str


@dataclass(frozen=True)
class MultiChoiceSpecificationValue:
    choices: tuple[str, ...]


@dataclass(frozen=True)
class NullSpecificationValue:
    is_null: bool = True


@dataclass(frozen=True)
class UninterpretedSpecificationValue:
    json: object


def fields_for_fieldset(
    fieldset: PersistedFieldsetDTO,
    graph: LoadedSpecificationGraphDTO,
) -> tuple[FieldDefinitionDTO, ...]:
    """Join already-loaded Field memberships without touching the ORM."""
    fields_by_identity = {str(field.identity): field for field in graph.fields_by_key.values()}
    fields: list[FieldDefinitionDTO] = []
    for membership in fieldset.field_memberships:
        field = fields_by_identity.get(str(membership.field_identity))
        if field is None:
            raise ValueError(f"unresolved Field membership: {membership.field_identity}")
        fields.append(field)
    return tuple(fields)


def specification_value_for_entry(entry: SpecificationProjectionEntryDTO) -> object:
    """Return the typed union arm, preserving unknown/invalid history as JSON."""
    value = entry.value
    if value is None:
        return NullSpecificationValue()
    if entry.definition is None or entry.state in {"invalid", "unknown"}:
        return UninterpretedSpecificationValue(json=value)

    field_type = entry.definition.field_type
    if field_type == "text" and type(value) is str:
        return TextSpecificationValue(text=value)
    if field_type == "integer" and type(value) is int and not isinstance(value, bool):
        return IntegerSpecificationValue(integer=value)
    if field_type == "decimal" and type(value) is str:
        return DecimalSpecificationValue(decimal=value)
    if field_type == "boolean" and type(value) is bool:
        return BooleanSpecificationValue(boolean=value)
    if field_type == "date" and type(value) is str:
        return DateSpecificationValue(date=value)
    if field_type == "single_select" and type(value) is str:
        return ChoiceSpecificationValue(choice=value)
    if field_type == "multi_select" and type(value) in {list, tuple} and all(type(item) is str for item in value):
        return MultiChoiceSpecificationValue(choices=tuple(value))
    return UninterpretedSpecificationValue(json=value)


def issues_for_entries(entries: tuple[SpecificationProjectionEntryDTO, ...]) -> tuple[UserErrorView, ...]:
    """Expose diagnostics without copying stored values into an error payload."""
    issues: list[UserErrorView] = []
    for entry in entries:
        for reason in entry.reason_codes:
            if reason in {"ACTIVE_VALUE"}:
                continue
            issues.append(
                UserErrorView(
                    code=reason,
                    path=("specifications", str(entry.key)),
                    field_key=str(entry.key),
                    message=f"specifications.{reason.lower()}",
                )
            )
    return tuple(issues)


def issues_for_missing_required(issues: tuple[ProjectionIssueDTO, ...]) -> tuple[UserErrorView, ...]:
    return tuple(
        UserErrorView(
            code="MISSING_REQUIRED",
            path=("specifications", str(issue.field_key)),
            field_key=str(issue.field_key),
            message="specifications.missing_required",
        )
        for issue in issues
    )


def field_view_sources(field: FieldDefinitionDTO | ResolvedFieldDTO) -> tuple[str, ...]:
    if not isinstance(field, ResolvedFieldDTO):
        return ()
    return tuple(str(identity) for identity in field.contributing_section_identities)


__all__ = [
    "UserErrorView",
    "field_view_sources",
    "fields_for_fieldset",
    "issues_for_entries",
    "issues_for_missing_required",
    "specification_value_for_entry",
]
