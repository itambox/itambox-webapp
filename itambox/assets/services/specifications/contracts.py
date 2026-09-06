"""Canonical request DTOs for specification service operations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NewType, TypeAlias

from extras.services.specifications.contracts import (
    DefinitionRevision,
    FieldDefinitionDTO,
    FieldKey,
    JSONValue,
    LoadedSpecificationGraphDTO,
    OrderedFieldsetMembershipDTO,
    QualifiedIdentity,
    ResourceRevision,
    SpecificationDefinitionDTO,
    StoredSpecificationEntryDTO,
    TargetKind,
)

AssetId = NewType("AssetId", int)
AssetTypeId = NewType("AssetTypeId", int)
CategoryId = NewType("CategoryId", int)
ManufacturerId = NewType("ManufacturerId", int)
AssetRoleId = NewType("AssetRoleId", int)
DepreciationId = NewType("DepreciationId", int)
TagId = NewType("TagId", int)
StagedImageId = NewType("StagedImageId", str)
PreviewToken = NewType("PreviewToken", str)
CategoryDefaultSnapshotRevision = NewType("CategoryDefaultSnapshotRevision", str)

_QUALIFIED_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[a-z0-9][a-z0-9._-]{0,126}$")
# Server-created opaque stage reference: 32 lowercase hex characters produced by
# ``secrets.token_hex(16)`` in the Assets staging authority. The grammar is
# intentionally narrow and ASCII-safe; the value is never interpreted as a path.
_STAGED_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _freeze_patch_value(value: JSONValue) -> JSONValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_patch_value(nested) for key, nested in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_patch_value(nested) for nested in value)
    return value


@dataclass(frozen=True)
class SpecificationPatchDTO:
    """The only ordinary value-writer input.

    ``set_values`` is recursively detached and frozen at the boundary.  The
    values themselves are already parsed JSON values; normalization belongs to
    the pure Extras codec and therefore remains operation-specific in the
    command layer.
    """

    set_values: Mapping[FieldKey, JSONValue]
    clear_keys: tuple[FieldKey, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.set_values, Mapping):
            raise TypeError("set_values must be a mapping")
        if type(self.clear_keys) is not tuple:
            raise TypeError("clear_keys must be a tuple")
        for key in self.set_values:
            if type(key) is not str or not key:
                raise ValueError("set_values keys must be non-empty strings")
        for key in self.clear_keys:
            if type(key) is not str or not key:
                raise ValueError("clear_keys must contain non-empty strings")
        object.__setattr__(self, "set_values", _freeze_patch_value(self.set_values))


@dataclass(frozen=True)
class ExplicitFieldsetSelectionDTO:
    """An explicit, ordered Fieldset replacement; omission is not representable."""

    identities: tuple[QualifiedIdentity, ...]

    def __post_init__(self) -> None:
        if type(self.identities) is not tuple:
            raise TypeError("identities must be a tuple")
        seen: set[str] = set()
        for identity in self.identities:
            if type(identity) is not str or _QUALIFIED_IDENTITY_RE.fullmatch(identity) is None:
                raise ValueError("identities must contain well-formed qualified Fieldset identities")
            if identity in seen:
                raise ValueError(f"duplicate Fieldset identity: {identity}")
            seen.add(identity)


@dataclass(frozen=True)
class DestinationAssetTypeSelectionDTO:
    """Explicit destination semantics for an Asset specification mutation."""

    presence: Literal["keep_current", "replace"]
    asset_type_id: AssetTypeId | None

    def __post_init__(self) -> None:
        if self.presence == "keep_current":
            if self.asset_type_id is not None:
                raise ValueError("keep_current destination cannot carry an Asset Type ID")
            return
        if self.presence != "replace":
            raise ValueError(f"unsupported destination presence: {self.presence!r}")
        if self.asset_type_id is None or type(self.asset_type_id) is not int or self.asset_type_id <= 0:
            raise ValueError("replace destination requires a positive Asset Type ID")


def _validate_positive_or_none(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")


def _validate_non_negative_or_none(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


def _validate_optional_bounded_string(value: object, name: str) -> None:
    # The DTO boundary stays syntactic; model-limit parity (slug grammar,
    # per-field lengths) is enforced by the shared native planning helper.
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > 255:
        raise ValueError(f"{name} must not exceed 255 characters")


def _validate_string_fields(instance: "AssetTypeNativeCreateInputDTO") -> None:
    for name in ("part_number", "ean", "region", "configuration", "description", "comments"):
        if type(getattr(instance, name)) is not str:
            raise TypeError(f"{name} must be a string")


def _validate_tag_ids(tag_ids: tuple[object, ...]) -> None:
    seen: set[int] = set()
    for tag_id in tag_ids:
        if type(tag_id) is not int or tag_id <= 0:
            raise ValueError("tag_ids must contain positive integers")
        if tag_id in seen:
            raise ValueError(f"duplicate tag id: {tag_id}")
        seen.add(tag_id)


@dataclass(frozen=True)
class FieldsetSelectionDTO:
    """Presence-sensitive Fieldset selection for Type creation.

    ``presence="omitted"`` is the only omitted value and must carry no
    identities; ``presence="explicit"`` permits an empty or ordered nonempty
    tuple of well-formed qualified Fieldset identities without duplicates.
    Any list/null input is rejected before construction.
    """

    presence: Literal["omitted", "explicit"]
    identities: tuple[QualifiedIdentity, ...]

    def __post_init__(self) -> None:
        if self.presence not in {"omitted", "explicit"}:
            raise ValueError(f"unsupported selection presence: {self.presence!r}")
        if type(self.identities) is not tuple:
            raise TypeError("identities must be a tuple")
        if self.presence == "omitted":
            if self.identities:
                raise ValueError("omitted selection cannot carry identities")
            return
        seen: set[str] = set()
        for identity in self.identities:
            if type(identity) is not str or _QUALIFIED_IDENTITY_RE.fullmatch(identity) is None:
                raise ValueError("identities must contain well-formed qualified Fieldset identities")
            if identity in seen:
                raise ValueError(f"duplicate Fieldset identity: {identity}")
            seen.add(identity)


@dataclass(frozen=True)
class AssetTypeNativeCreateInputDTO:
    """Native, server-validated Asset Type create input.

    Adapters construct this DTO at the transport boundary; they never pass a
    dictionary, model instance, request, or uploaded-file object into the
    command. ``staged_image_id`` is a bounded server-created staging reference
    only; the command consumes or discards it atomically.
    """

    manufacturer_id: ManufacturerId
    model: str
    slug: str | None
    part_number: str
    ean: str
    region: str
    configuration: str
    eol_months: int | None
    category_id: CategoryId | None
    suggested_asset_role_id: AssetRoleId | None
    depreciation_id: DepreciationId | None
    staged_image_id: StagedImageId | None
    description: str
    comments: str
    tag_ids: tuple[TagId, ...]
    requestable: bool

    def __post_init__(self) -> None:
        if type(self.manufacturer_id) is not int or self.manufacturer_id <= 0:
            raise ValueError("manufacturer_id must be a positive integer")
        if type(self.model) is not str or not self.model:
            raise ValueError("model must be a non-empty string")
        if self.slug is not None:
            _validate_optional_bounded_string(self.slug, "slug")
        _validate_string_fields(self)
        _validate_non_negative_or_none(self.eol_months, "eol_months")
        _validate_positive_or_none(self.category_id, "category_id")
        _validate_positive_or_none(self.suggested_asset_role_id, "suggested_asset_role_id")
        _validate_positive_or_none(self.depreciation_id, "depreciation_id")
        if self.staged_image_id is not None:
            if type(self.staged_image_id) is not str or _STAGED_IMAGE_ID_RE.fullmatch(self.staged_image_id) is None:
                raise ValueError("staged_image_id must be a bounded server-created opaque stage reference")
        if type(self.tag_ids) is not tuple:
            raise TypeError("tag_ids must be a tuple")
        _validate_tag_ids(self.tag_ids)
        if type(self.requestable) is not bool:
            raise TypeError("requestable must be a bool")


DomainIssueCode: TypeAlias = Literal[
    "INVALID_TYPE",
    "INVALID_DECIMAL",
    "INVALID_RANGE",
    "INVALID_DATE",
    "INVALID_CHOICE",
    "REQUIRED_FIELD",
    "UNKNOWN_FIELD_KEY",
    "READ_ONLY_FIELD",
    "CONFLICT_CLEAR_OVERLAP",
    "DUPLICATE_FIELD",
    "IMMUTABLE_DEFINITION",
    "OWNERSHIP_CONFLICT",
    "REFERENCE_CONFLICT",
    "DEPENDENCY_RETIREMENT",
    "UNSUPPORTED_STRUCTURE",
    "STALE_RESOURCE",
    "STALE_DEFINITION",
    "STALE_PLAN",
    "EXPORT_BLOCKED",
    "OBJECT_UNAVAILABLE",
    "MISSING_PRECONDITION",
]


@dataclass(frozen=True)
class DomainIssueDTO:
    code: DomainIssueCode
    path: tuple[str, ...]
    field_key: FieldKey | None
    message_key: str


@dataclass(frozen=True)
class OwnerRefDTO:
    owner_kind: Literal["asset_type", "asset", "category"]
    owner_id: int

    def __post_init__(self) -> None:
        if self.owner_kind not in {"asset_type", "asset", "category"}:
            raise ValueError(f"unsupported owner kind: {self.owner_kind!r}")
        if type(self.owner_id) is not int or self.owner_id <= 0:
            raise ValueError("owner_id must be a positive integer")


@dataclass(frozen=True)
class OwnerChangedDTO:
    outcome: Literal["changed"]
    owner: OwnerRefDTO
    resource_revision: ResourceRevision
    definition_revision: DefinitionRevision


@dataclass(frozen=True)
class OwnerNoOpDTO:
    outcome: Literal["no_op"]
    owner: OwnerRefDTO
    resource_revision: ResourceRevision
    definition_revision: DefinitionRevision


@dataclass(frozen=True)
class OwnerCreatedDTO:
    outcome: Literal["created"]
    owner: OwnerRefDTO
    resource_revision: ResourceRevision
    definition_revision: DefinitionRevision


@dataclass(frozen=True)
class CategoryDefaultSnapshotDTO:
    """Distinct Category-default snapshot source state recomputed under the lock."""

    category_id: CategoryId
    revision: CategoryDefaultSnapshotRevision
    memberships: tuple[OrderedFieldsetMembershipDTO, ...]


@dataclass(frozen=True)
class AssetTypePreviewDTO:
    preview_token: PreviewToken | None
    definition: SpecificationDefinitionDTO
    expected_definition_revision: DefinitionRevision
    expected_resource_revision: ResourceRevision | None
    expected_category_default_snapshot_revision: CategoryDefaultSnapshotRevision | None
    consumes_category_defaults: bool
    issues: tuple[DomainIssueDTO, ...]


@dataclass(frozen=True)
class CommandRejectedDTO:
    outcome: Literal["rejected"]
    safe_owner: OwnerRefDTO | None
    issues: tuple[DomainIssueDTO, ...]


OwnerMutationResult: TypeAlias = OwnerChangedDTO | OwnerNoOpDTO | CommandRejectedDTO
AssetTypeCreateResult: TypeAlias = OwnerCreatedDTO | CommandRejectedDTO
AssetTypePreviewResult: TypeAlias = AssetTypePreviewDTO | CommandRejectedDTO


@dataclass(frozen=True)
class SpecificationGraphLoadRequest:
    """Inputs for the batched, Assets-owned specification graph loader."""

    asset_type_ids: tuple[AssetTypeId, ...]
    requested_target_kinds: frozenset[TargetKind]
    requested_field_keys: frozenset[FieldKey]


@dataclass(frozen=True)
class SpecificationResolutionRequest:
    """Loaded graph and ordered memberships for pure definition resolution."""

    ordered_memberships: tuple[OrderedFieldsetMembershipDTO, ...]
    loaded_graph: LoadedSpecificationGraphDTO
    target_kind: TargetKind


@dataclass(frozen=True)
class SpecificationProjectionRequest:
    """Definition and stored entries for pure historical-value projection."""

    definition: SpecificationDefinitionDTO
    stored_entries: tuple[StoredSpecificationEntryDTO, ...]
    historical_definitions_by_key: Mapping[FieldKey, FieldDefinitionDTO]
