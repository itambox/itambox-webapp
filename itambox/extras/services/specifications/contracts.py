"""Pure DTOs for specification definitions and value projection."""

from dataclasses import dataclass
from typing import Literal, Mapping, NewType, TypeAlias

FieldKey = NewType("FieldKey", str)
QualifiedIdentity = NewType("QualifiedIdentity", str)
ResourceRevision = NewType("ResourceRevision", str)
DefinitionRevision = NewType("DefinitionRevision", str)
DecimalString = NewType("DecimalString", str)
TargetKind: TypeAlias = Literal["asset_type", "asset"]
FieldType: TypeAlias = Literal["text", "integer", "decimal", "boolean", "date", "single_select", "multi_select"]
DefinitionLifecycle: TypeAlias = Literal["active", "deprecated"]
FieldActivation: TypeAlias = Literal["composed", "global"]
JSONScalar: TypeAlias = str | int | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
ProjectionState: TypeAlias = Literal["current", "historical", "invalid", "unknown"]
ProjectionReasonCode: TypeAlias = Literal[
    "ACTIVE_VALUE",
    "INACTIVE_COMPOSITION",
    "DEPRECATED_FIELD",
    "DEPRECATED_CHOICE",
    "INVALID_STORED_VALUE",
    "UNKNOWN_DEFINITION",
    "MISSING_REQUIRED",
]


@dataclass(frozen=True)
class SpecificationValidationDTO:
    minimum: DecimalString | None
    maximum: DecimalString | None
    scale: int | None
    max_length: int | None
    max_values: int | None
    regex: str | None
    rule: str | None


@dataclass(frozen=True)
class ChoiceDTO:
    key: str
    label: str
    lifecycle: DefinitionLifecycle
    position: int


@dataclass(frozen=True)
class ChoiceSetDTO:
    identity: QualifiedIdentity
    label: str
    resource_revision: ResourceRevision
    lifecycle: DefinitionLifecycle
    choices: tuple[ChoiceDTO, ...]


@dataclass(frozen=True)
class FieldDefinitionDTO:
    resource_revision: ResourceRevision
    key: FieldKey
    identity: QualifiedIdentity
    label: str
    help_text: str
    targets: frozenset[TargetKind]
    activation: FieldActivation
    field_type: FieldType
    quantity_kind: str | None
    canonical_unit: str | None
    validation: SpecificationValidationDTO
    required: bool
    nullable: bool
    lifecycle: DefinitionLifecycle
    choice_set: ChoiceSetDTO | None


@dataclass(frozen=True)
class OrderedFieldMembershipDTO:
    field_identity: QualifiedIdentity
    ordinal: int


@dataclass(frozen=True)
class PersistedFieldsetDTO:
    identity: QualifiedIdentity
    label: str
    description: str
    resource_revision: ResourceRevision
    lifecycle: DefinitionLifecycle
    field_memberships: tuple[OrderedFieldMembershipDTO, ...]


@dataclass(frozen=True)
class OrderedFieldsetMembershipDTO:
    fieldset_identity: QualifiedIdentity
    ordinal: int


@dataclass(frozen=True)
class ResolvedFieldDTO:
    resource_revision: ResourceRevision
    key: FieldKey
    identity: QualifiedIdentity
    label: str
    help_text: str
    targets: frozenset[TargetKind]
    activation: FieldActivation
    field_type: FieldType
    quantity_kind: str | None
    canonical_unit: str | None
    validation: SpecificationValidationDTO
    required: bool
    nullable: bool
    lifecycle: DefinitionLifecycle
    choice_set: ChoiceSetDTO | None
    first_placement_section_identity: QualifiedIdentity | None
    contributing_section_identities: tuple[QualifiedIdentity, ...]


@dataclass(frozen=True)
class ResolvedSectionDTO:
    section_kind: Literal["persisted_fieldset", "derived_additional"]
    identity: QualifiedIdentity | None
    label: str
    description: str
    persisted_ordinal: int | None
    fields: tuple[ResolvedFieldDTO, ...]


@dataclass(frozen=True)
class LoadedSpecificationGraphDTO:
    type_memberships: Mapping[int, tuple[OrderedFieldsetMembershipDTO, ...]]
    fieldsets_by_identity: Mapping[QualifiedIdentity, PersistedFieldsetDTO]
    fields_by_key: Mapping[FieldKey, FieldDefinitionDTO]
    global_field_keys_by_target: Mapping[TargetKind, tuple[FieldKey, ...]]
    historical_definitions_by_key: Mapping[FieldKey, FieldDefinitionDTO]


@dataclass(frozen=True)
class SpecificationDefinitionDTO:
    revision: DefinitionRevision
    target_kind: TargetKind
    persisted_memberships: tuple[OrderedFieldsetMembershipDTO, ...]
    rendered_sections: tuple[ResolvedSectionDTO, ...]


@dataclass(frozen=True)
class StoredSpecificationEntryDTO:
    key: FieldKey
    value: JSONValue


@dataclass(frozen=True)
class SpecificationProjectionEntryDTO:
    key: FieldKey
    value: JSONValue
    state: ProjectionState
    reason_codes: tuple[ProjectionReasonCode, ...]
    definition: FieldDefinitionDTO | None


@dataclass(frozen=True)
class ProjectionIssueDTO:
    code: ProjectionReasonCode
    field_key: FieldKey


@dataclass(frozen=True)
class SpecificationProjectionDTO:
    entries: tuple[SpecificationProjectionEntryDTO, ...]
    missing_required_issues: tuple[ProjectionIssueDTO, ...]
