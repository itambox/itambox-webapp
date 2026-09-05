"""Canonical request DTOs for specification service operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NewType

from extras.services.specifications.contracts import (
    FieldDefinitionDTO,
    FieldKey,
    LoadedSpecificationGraphDTO,
    OrderedFieldsetMembershipDTO,
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
