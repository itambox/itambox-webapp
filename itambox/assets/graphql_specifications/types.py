"""Explicit Graphene read types backed by specification DTOs.

These types intentionally expose only the frozen specification vocabulary.  They
do not inherit Django model fields and they never perform ORM work from a Field
or Choice resolver.
"""

from __future__ import annotations

from dataclasses import dataclass

import graphene

from extras.services.specifications.contracts import (
    FieldDefinitionDTO,
    PersistedFieldsetDTO,
    ResolvedFieldDTO,
    ResolvedSectionDTO,
    SpecificationDefinitionDTO,
    SpecificationProjectionEntryDTO,
)

from .readers import (
    BooleanSpecificationValue,
    ChoiceSpecificationValue,
    DateSpecificationValue,
    DecimalSpecificationValue,
    IntegerSpecificationValue,
    MultiChoiceSpecificationValue,
    NullSpecificationValue,
    TextSpecificationValue,
    UninterpretedSpecificationValue,
    specification_value_for_entry,
)
from .scalars import CursorScalar, DateScalar, DecimalScalar, JSONScalar, SafeInteger


class SpecificationTargetEnum(graphene.Enum):
    ASSET_TYPE = "asset_type"
    ASSET = "asset"

    class Meta:
        name = "SpecificationTarget"


class ScopeModeEnum(graphene.Enum):
    TENANT = "tenant"
    TENANT_GROUP = "tenant_group"
    ALL_ACCESSIBLE = "all_accessible"

    class Meta:
        name = "ScopeMode"


class SpecificationFieldTypeEnum(graphene.Enum):
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"

    class Meta:
        name = "SpecificationFieldType"


class DefinitionLifecycleEnum(graphene.Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"

    class Meta:
        name = "DefinitionLifecycle"


class FieldActivationEnum(graphene.Enum):
    COMPOSED = "composed"
    GLOBAL = "global"

    class Meta:
        name = "FieldActivation"


class SpecificationEntryStateEnum(graphene.Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    INVALID = "invalid"
    UNKNOWN = "unknown"

    class Meta:
        name = "SpecificationEntryState"


class LibraryReconciliationStateEnum(graphene.Enum):
    UNCHANGED = "unchanged"
    LOCALLY_MODIFIED = "locally_modified"
    UNRECONCILED = "unreconciled"
    RETAINED_HISTORY = "retained_history"

    class Meta:
        name = "LibraryReconciliationState"


class ChoiceType(graphene.ObjectType):
    class Meta:
        name = "Choice"

    key = graphene.String(required=True)
    label = graphene.String(required=True)
    lifecycle = graphene.Field(DefinitionLifecycleEnum, required=True)


class ChoiceSetType(graphene.ObjectType):
    class Meta:
        name = "ChoiceSet"

    identity = graphene.String(required=True)
    label = graphene.String(required=True)
    resource_revision = graphene.String(required=True)
    lifecycle = graphene.Field(DefinitionLifecycleEnum, required=True)
    choices = graphene.List(graphene.NonNull(ChoiceType), required=True)


class SpecificationValidationType(graphene.ObjectType):
    class Meta:
        name = "SpecificationValidation"

    minimum = graphene.Field(DecimalScalar)
    maximum = graphene.Field(DecimalScalar)
    scale = graphene.Int()
    max_length = graphene.Int()
    max_values = graphene.Int()
    regex = graphene.String()
    rule = graphene.String()


class SpecificationFieldNode(graphene.ObjectType):
    class Meta:
        name = "SpecificationField"

    resource_revision = graphene.String(required=True)
    key = graphene.String(required=True)
    identity = graphene.String(required=True)
    label = graphene.String(required=True)
    help_text = graphene.String(required=True)
    targets = graphene.List(graphene.NonNull(SpecificationTargetEnum), required=True)
    activation = graphene.Field(FieldActivationEnum, required=True)
    field_type = graphene.Field(SpecificationFieldTypeEnum, required=True)
    required = graphene.Boolean(required=True)
    nullable = graphene.Boolean(required=True)
    quantity_kind = graphene.String()
    canonical_unit = graphene.String()
    validation = graphene.Field(SpecificationValidationType, required=True)
    lifecycle = graphene.Field(DefinitionLifecycleEnum, required=True)
    choice_set = graphene.Field(ChoiceSetType)
    sources = graphene.List(graphene.NonNull(graphene.String), required=True)

    @staticmethod
    def resolve_targets(field: FieldDefinitionDTO | ResolvedFieldDTO, info):
        del info
        return tuple(sorted(field.targets))

    @staticmethod
    def resolve_sources(field: FieldDefinitionDTO | ResolvedFieldDTO, info):
        del info
        return tuple(str(identity) for identity in getattr(field, "contributing_section_identities", ()))


@dataclass(frozen=True)
class FieldsetView:
    definition: PersistedFieldsetDTO
    fields: tuple[FieldDefinitionDTO | ResolvedFieldDTO, ...]


class SpecificationFieldsetType(graphene.ObjectType):
    class Meta:
        name = "SpecificationFieldset"

    resource_revision = graphene.String(required=True)
    identity = graphene.String(required=True)
    label = graphene.String(required=True)
    description = graphene.String(required=True)
    lifecycle = graphene.Field(DefinitionLifecycleEnum, required=True)
    fields = graphene.List(graphene.NonNull(SpecificationFieldNode), required=True)

    @staticmethod
    def resolve_resource_revision(fieldset: FieldsetView, info):
        del info
        return fieldset.definition.resource_revision

    @staticmethod
    def resolve_identity(fieldset: FieldsetView, info):
        del info
        return fieldset.definition.identity

    @staticmethod
    def resolve_label(fieldset: FieldsetView, info):
        del info
        return fieldset.definition.label

    @staticmethod
    def resolve_description(fieldset: FieldsetView, info):
        del info
        return fieldset.definition.description

    @staticmethod
    def resolve_lifecycle(fieldset: FieldsetView, info):
        del info
        return fieldset.definition.lifecycle

    @staticmethod
    def resolve_fields(fieldset: FieldsetView, info):
        del info
        return fieldset.fields


class SpecificationSectionType(graphene.ObjectType):
    class Meta:
        name = "SpecificationSection"

    identity = graphene.String()
    label = graphene.String(required=True)
    fields = graphene.List(graphene.NonNull(SpecificationFieldNode), required=True)

    @staticmethod
    def resolve_fields(section: ResolvedSectionDTO, info):
        del info
        return section.fields


class SpecificationDefinitionType(graphene.ObjectType):
    class Meta:
        name = "SpecificationDefinition"

    revision = graphene.String(required=True)
    target = graphene.Field(SpecificationTargetEnum, required=True)
    sections = graphene.List(graphene.NonNull(SpecificationSectionType), required=True)

    @staticmethod
    def resolve_target(definition: SpecificationDefinitionDTO, info):
        del info
        return definition.target_kind

    @staticmethod
    def resolve_sections(definition: SpecificationDefinitionDTO, info):
        del info
        return definition.rendered_sections


class TextSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "TextSpecificationValue"

    text = graphene.String(required=True)


class IntegerSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "IntegerSpecificationValue"

    integer = graphene.Field(SafeInteger, required=True)


class DecimalSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "DecimalSpecificationValue"

    decimal = graphene.Field(DecimalScalar, required=True)


class BooleanSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "BooleanSpecificationValue"

    boolean = graphene.Boolean(required=True)


class DateSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "DateSpecificationValue"

    date = graphene.Field(DateScalar, required=True)


class ChoiceSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "ChoiceSpecificationValue"

    choice = graphene.String(required=True)


class MultiChoiceSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "MultiChoiceSpecificationValue"

    choices = graphene.List(graphene.NonNull(graphene.String), required=True)


class NullSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "NullSpecificationValue"

    is_null = graphene.Boolean(required=True)


class UninterpretedSpecificationValueType(graphene.ObjectType):
    class Meta:
        name = "UninterpretedSpecificationValue"

    json = graphene.Field(JSONScalar, required=True)


class SpecificationValueUnion(graphene.Union):
    class Meta:
        name = "SpecificationValue"
        types = (
            TextSpecificationValueType,
            IntegerSpecificationValueType,
            DecimalSpecificationValueType,
            BooleanSpecificationValueType,
            DateSpecificationValueType,
            ChoiceSpecificationValueType,
            MultiChoiceSpecificationValueType,
            NullSpecificationValueType,
            UninterpretedSpecificationValueType,
        )

    @classmethod
    def resolve_type(cls, instance, info):
        del info
        type_map = {
            TextSpecificationValue: TextSpecificationValueType,
            IntegerSpecificationValue: IntegerSpecificationValueType,
            DecimalSpecificationValue: DecimalSpecificationValueType,
            BooleanSpecificationValue: BooleanSpecificationValueType,
            DateSpecificationValue: DateSpecificationValueType,
            ChoiceSpecificationValue: ChoiceSpecificationValueType,
            MultiChoiceSpecificationValue: MultiChoiceSpecificationValueType,
            NullSpecificationValue: NullSpecificationValueType,
            UninterpretedSpecificationValue: UninterpretedSpecificationValueType,
        }
        for python_type, graphql_type in type_map.items():
            if isinstance(instance, python_type):
                return graphql_type
        return None


class SpecificationEntryType(graphene.ObjectType):
    class Meta:
        name = "SpecificationEntry"

    key = graphene.String(required=True)
    definition = graphene.Field(SpecificationFieldNode)
    state = graphene.Field(SpecificationEntryStateEnum, required=True)
    reason_codes = graphene.List(graphene.NonNull(graphene.String), required=True)
    value = graphene.Field(SpecificationValueUnion, required=True)

    @staticmethod
    def resolve_reason_codes(entry: SpecificationProjectionEntryDTO, info):
        del info
        return tuple(entry.reason_codes)

    @staticmethod
    def resolve_value(entry: SpecificationProjectionEntryDTO, info):
        del info
        return specification_value_for_entry(entry)


@dataclass(frozen=True)
class LibraryOriginView:
    identity: str
    accepted_release: int | None
    state: str


class LibraryOriginType(graphene.ObjectType):
    class Meta:
        name = "LibraryOrigin"

    identity = graphene.String(required=True)
    accepted_release = graphene.Field(SafeInteger)
    state = graphene.Field(LibraryReconciliationStateEnum, required=True)


class UserErrorType(graphene.ObjectType):
    class Meta:
        name = "UserError"

    code = graphene.String(required=True)
    path = graphene.List(graphene.NonNull(graphene.String), required=True)
    field_key = graphene.String()
    message = graphene.String(required=True)


class PageInfoType(graphene.ObjectType):
    class Meta:
        name = "PageInfo"

    end_cursor = graphene.Field(CursorScalar)
    has_next_page = graphene.Boolean(required=True)


@dataclass(frozen=True)
class AssetTypeEdgeView:
    cursor: str
    node: object


@dataclass(frozen=True)
class AssetTypeConnectionView:
    edges: tuple[AssetTypeEdgeView, ...]
    page_info: PageInfoType


@dataclass(frozen=True)
class SpecificationFieldEdgeView:
    cursor: str
    node: FieldDefinitionDTO


@dataclass(frozen=True)
class SpecificationFieldConnectionView:
    edges: tuple[SpecificationFieldEdgeView, ...]
    page_info: PageInfoType


class SpecificationFieldEdgeType(graphene.ObjectType):
    class Meta:
        name = "SpecificationFieldEdge"

    cursor = graphene.Field(CursorScalar, required=True)
    node = graphene.Field(SpecificationFieldNode, required=True)


class SpecificationFieldConnectionType(graphene.ObjectType):
    class Meta:
        name = "SpecificationFieldConnection"

    edges = graphene.List(graphene.NonNull(SpecificationFieldEdgeType), required=True)
    page_info = graphene.Field(PageInfoType, required=True)


__all__ = [
    "BooleanSpecificationValue",
    "ChoiceSetType",
    "ChoiceSpecificationValue",
    "ChoiceSpecificationValueType",
    "ChoiceType",
    "CursorScalar",
    "DateScalar",
    "DateSpecificationValue",
    "DateSpecificationValueType",
    "DecimalSpecificationValue",
    "DecimalSpecificationValueType",
    "DefinitionLifecycleEnum",
    "FieldActivationEnum",
    "FieldsetView",
    "IntegerSpecificationValue",
    "IntegerSpecificationValueType",
    "JSONScalar",
    "LibraryOriginType",
    "LibraryOriginView",
    "LibraryReconciliationStateEnum",
    "MultiChoiceSpecificationValue",
    "MultiChoiceSpecificationValueType",
    "NullSpecificationValue",
    "NullSpecificationValueType",
    "PageInfoType",
    "SpecificationDefinitionType",
    "SpecificationEntryStateEnum",
    "SpecificationEntryType",
    "SpecificationFieldConnectionType",
    "SpecificationFieldEdgeType",
    "SpecificationFieldNode",
    "SpecificationFieldTypeEnum",
    "SpecificationFieldsetType",
    "SpecificationSectionType",
    "ScopeModeEnum",
    "SpecificationTargetEnum",
    "SpecificationValidationType",
    "SpecificationValueUnion",
    "TextSpecificationValue",
    "TextSpecificationValueType",
    "UninterpretedSpecificationValue",
    "UninterpretedSpecificationValueType",
    "UserErrorType",
]
