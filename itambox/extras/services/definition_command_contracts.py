"""Immutable typed inputs and results for globally authorized definition commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, TypeAlias

from assets.services.specifications.contracts import DomainIssueDTO
from extras.services.specifications.contracts import JSONValue, QualifiedIdentity, ResourceRevision

DefinitionKind: TypeAlias = Literal["field", "fieldset", "choice_set", "choice"]
DefinitionLifecycle: TypeAlias = Literal["active", "deprecated"]
FieldType: TypeAlias = Literal["text", "integer", "decimal", "date", "boolean", "single-select", "multi-select"]
FieldActivation: TypeAlias = Literal["composed", "global"]


def _freeze_mapping_value(value: JSONValue) -> JSONValue:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("mapping object keys must be strings")
        return MappingProxyType({key: _freeze_mapping_value(nested) for key, nested in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_mapping_value(nested) for nested in value)
    if value is None or type(value) in {str, bool, int, float}:
        return value
    raise TypeError("mappings must contain only JSON values")


@dataclass(frozen=True)
class CustomFieldCreateInputDTO:
    namespace: str
    local_key: str
    label: str
    object_types: tuple[str, ...]
    field_type: FieldType = "text"
    activation: FieldActivation = "global"
    help_text: str = ""
    quantity_kind: str | None = None
    canonical_unit: str | None = None
    minimum_value: Decimal | None = None
    maximum_value: Decimal | None = None
    regex: str | None = None
    decimal_scale: int | None = None
    max_values: int | None = None
    text_max_length: int | None = None
    validation_rule: str | None = None
    required: bool = False
    nullable: bool = False
    mappings: tuple[JSONValue, ...] = ()
    choice_set_id: int | None = None
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.namespace, "namespace")
        _require_nonempty_string(self.local_key, "local_key")
        _require_string(self.label, "label")
        _require_string(self.help_text, "help_text")
        _require_string_tuple(self.object_types, "object_types")
        _require_tuple(self.mappings, "mappings")
        object.__setattr__(self, "mappings", tuple(_freeze_mapping_value(item) for item in self.mappings))
        _require_bool(self.required, "required")
        _require_bool(self.nullable, "nullable")
        _require_positive_or_none(self.choice_set_id, "choice_set_id")


@dataclass(frozen=True)
class CustomFieldUpdateInputDTO:
    label: str | None = None
    help_text: str | None = None
    activation: FieldActivation | None = None
    required: bool | None = None
    mappings: tuple[JSONValue, ...] | None = None
    object_types: tuple[str, ...] | None = None
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_string_or_none(self.label, "label")
        _require_string_or_none(self.help_text, "help_text")
        if self.required is not None:
            _require_bool(self.required, "required")
        if self.mappings is not None:
            _require_tuple(self.mappings, "mappings")
            object.__setattr__(self, "mappings", tuple(_freeze_mapping_value(item) for item in self.mappings))
        if self.object_types is not None:
            _require_string_tuple(self.object_types, "object_types")


@dataclass(frozen=True)
class CustomFieldsetCreateInputDTO:
    namespace: str
    slug: str
    label: str = ""
    description: str = ""
    field_identities: tuple[QualifiedIdentity, ...] = ()
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.namespace, "namespace")
        _require_nonempty_string(self.slug, "slug")
        _require_string(self.label, "label")
        _require_string(self.description, "description")
        _require_identity_tuple(self.field_identities, "field_identities")


@dataclass(frozen=True)
class CustomFieldsetUpdateInputDTO:
    label: str | None = None
    description: str | None = None
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_string_or_none(self.label, "label")
        _require_string_or_none(self.description, "description")


@dataclass(frozen=True)
class CustomFieldChoiceSetCreateInputDTO:
    namespace: str
    slug: str
    label: str
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.namespace, "namespace")
        _require_nonempty_string(self.slug, "slug")
        _require_string(self.label, "label")


@dataclass(frozen=True)
class CustomFieldChoiceSetUpdateInputDTO:
    label: str | None = None
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_string_or_none(self.label, "label")


@dataclass(frozen=True)
class CustomFieldChoiceCreateInputDTO:
    choice_set_id: int
    key: str
    label: str
    position: int
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_positive(self.choice_set_id, "choice_set_id")
        _require_nonempty_string(self.key, "key")
        _require_string(self.label, "label")
        _require_positive(self.position, "position")


@dataclass(frozen=True)
class CustomFieldChoiceUpdateInputDTO:
    label: str | None = None
    position: int | None = None
    replaced_by: QualifiedIdentity | None = None

    def __post_init__(self) -> None:
        _require_string_or_none(self.label, "label")
        _require_positive_or_none(self.position, "position")


@dataclass(frozen=True)
class DefinitionSuccessDTO:
    outcome: Literal["created", "changed", "no_op"]
    definition_kind: DefinitionKind
    definition_id: int
    identity: QualifiedIdentity
    resource_revision: ResourceRevision
    lifecycle: DefinitionLifecycle
    version: int
    issues: tuple[DomainIssueDTO, ...] = ()


@dataclass(frozen=True)
class DefinitionRejectedDTO:
    outcome: Literal["rejected"]
    definition_kind: DefinitionKind | None
    definition_id: int | None
    identity: QualifiedIdentity | None
    issues: tuple[DomainIssueDTO, ...]


DefinitionCommandResult: TypeAlias = DefinitionSuccessDTO | DefinitionRejectedDTO


def _require_tuple(value: object, name: str) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")


def _require_string(value: object, name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")


def _require_nonempty_string(value: object, name: str) -> None:
    _require_string(value, name)
    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_string_or_none(value: object, name: str) -> None:
    if value is not None:
        _require_string(value, name)


def _require_bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool")


def _require_positive(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_positive_or_none(value: object, name: str) -> None:
    if value is not None:
        _require_positive(value, name)


def _require_string_tuple(value: object, name: str) -> None:
    _require_tuple(value, name)
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{name} must contain non-empty strings")


def _require_identity_tuple(value: object, name: str) -> None:
    _require_string_tuple(value, name)
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")


__all__ = [
    "CustomFieldChoiceCreateInputDTO",
    "CustomFieldChoiceSetCreateInputDTO",
    "CustomFieldChoiceSetUpdateInputDTO",
    "CustomFieldChoiceUpdateInputDTO",
    "CustomFieldCreateInputDTO",
    "CustomFieldUpdateInputDTO",
    "CustomFieldsetCreateInputDTO",
    "CustomFieldsetUpdateInputDTO",
    "DefinitionCommandResult",
    "DefinitionRejectedDTO",
    "DefinitionSuccessDTO",
]
