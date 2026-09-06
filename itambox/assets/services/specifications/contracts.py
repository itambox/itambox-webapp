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

_QUALIFIED_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[a-z0-9][a-z0-9._-]{0,126}$")


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
class CommandRejectedDTO:
    outcome: Literal["rejected"]
    safe_owner: OwnerRefDTO | None
    issues: tuple[DomainIssueDTO, ...]


OwnerMutationResult: TypeAlias = OwnerChangedDTO | OwnerNoOpDTO | CommandRejectedDTO


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
