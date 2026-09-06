"""Presentation-boundary adapters for the canonical specification commands.

Forms and serializers deliberately stop at this module.  They may translate
request/form values into immutable command DTOs, but they never merge or
persist ``custom_field_data`` themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError

from assets.models import Asset, AssetType
from assets.services.specifications._command_support import (
    load_effective_definition,
    resource_revision_for_owner,
    stored_values_for,
)
from assets.services.specifications._image_staging import (
    CREATE_COMMAND_KIND,
    discard_stage,
    ingest_staged_image,
)
from assets.services.specifications.contracts import (
    AssetTypeNativeCreateInputDTO,
    CommandRejectedDTO,
    DefinitionRevision,
    ExplicitFieldsetSelectionDTO,
    FieldsetSelectionDTO,
    ResourceRevision,
    SpecificationPatchDTO,
    StagedImageId,
)
from extras.customfields import is_omitted_optional_single_select
from extras.models import CustomFieldset
from organization.services.access_scope import (
    AccessScopeResolutionRequestDTO,
    AccessScopeResolvedDTO,
    ActorContextDTO,
    AccessScopeDeniedDTO,
    RequestedScopeSelectorDTO,
    ResolvedAccessAuthorizationDTO,
    TenantId,
    authentication_revision_for_actor,
    resolve_access_scope,
)


@dataclass(frozen=True)
class SpecificationPlan:
    """Fresh command preconditions loaded from the current owner row."""

    resource_revision: ResourceRevision
    definition_revision: DefinitionRevision


def actor_context_for_user(user: object) -> ActorContextDTO:
    """Build an actor DTO from a freshly available authenticated user object."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise PermissionDenied("An authenticated actor is required for specification changes.")
    return ActorContextDTO(
        actor_id=int(user.pk),
        authentication_revision=authentication_revision_for_actor(user),
    )


def authorization_for_asset(
    *,
    user: object,
    tenant_id: int | None,
) -> ResolvedAccessAuthorizationDTO:
    """Resolve the real Organization scope for one tenant-owned Asset."""
    actor = actor_context_for_user(user)
    if tenant_id is None:
        raise PermissionDenied("A tenant-bound Asset is required for specification changes.")
    request = AccessScopeResolutionRequestDTO(
        actor=actor,
        selector=RequestedScopeSelectorDTO(
            mode="tenant",
            tenant_id=TenantId(int(tenant_id)),
            tenant_group_id=None,
        ),
        operation="update_asset_specifications",
        required_permission="assets.change_asset",
    )
    resolved = resolve_access_scope(request)
    if isinstance(resolved, AccessScopeDeniedDTO):
        raise PermissionDenied("The actor is not authorized for this Asset tenant.")
    if not isinstance(resolved, AccessScopeResolvedDTO):
        raise PermissionDenied("The Asset authorization scope could not be resolved.")
    return ResolvedAccessAuthorizationDTO(
        actor=actor,
        request=request,
        initial_scope=resolved.access_scope,
    )


def specification_patch(
    *,
    definitions: Mapping[str, object],
    cleaned_values: Mapping[str, object],
    fields: Mapping[str, object] | None = None,
    clear_keys: Mapping[str, str] | None = None,
) -> SpecificationPatchDTO:
    """Translate cleaned form values into one explicit set/clear patch.

    Optional single-select omission remains an omission.  Every other submitted
    editable value is passed to the canonical codec, which is the sole authority
    for merge, validation, and historical-key preservation.
    """
    submitted: dict[str, object] = {}
    cleared: set[str] = set()
    clear_keys = clear_keys or {}
    field_map = fields or {}
    for key, definition in definitions.items():
        field = field_map.get(key)
        if field is not None and getattr(field, "disabled", False):
            continue
        if key not in cleaned_values:
            continue
        clear_key = clear_keys.get(key)
        if clear_key and cleaned_values.get(clear_key):
            cleared.add(getattr(definition, "name", key))
            continue
        value = cleaned_values[key]
        if is_omitted_optional_single_select(definition, value):
            continue
        if value is None and not getattr(definition, "nullable", False):
            continue
        submitted[getattr(definition, "name", key)] = value
    return SpecificationPatchDTO(
        set_values=submitted,
        clear_keys=tuple(sorted(cleared)),
    )


def patch_from_mapping(value: Mapping[str, object] | None) -> SpecificationPatchDTO:
    """Parse the transport-neutral API patch shape without persisting it."""
    if value is None:
        return SpecificationPatchDTO(set_values={}, clear_keys=())
    submitted = value.get("set", {})
    clear_keys = value.get("clear", [])
    if not isinstance(submitted, Mapping) or not isinstance(clear_keys, list):
        raise ValidationError("The specification patch must contain an object 'set' and list 'clear'.")
    if any(not isinstance(key, str) for key in clear_keys):
        raise ValidationError("Specification patch clear keys must be strings.")
    return SpecificationPatchDTO(
        set_values=dict(submitted),
        clear_keys=tuple(clear_keys),
    )


def fieldset_selection(
    fieldsets: Sequence[CustomFieldset],
    *,
    presence: str,
) -> ExplicitFieldsetSelectionDTO:
    """Build the ordered immutable selection used by composition commands."""
    identities = tuple(f"{fieldset.namespace}/{fieldset.slug}" for fieldset in fieldsets)
    return ExplicitFieldsetSelectionDTO(identities=identities)


def create_fieldset_selection(
    fieldsets: Sequence[CustomFieldset],
    *,
    omitted: bool,
) -> FieldsetSelectionDTO:
    if omitted:
        return FieldsetSelectionDTO(presence="omitted", identities=())
    identities = tuple(f"{fieldset.namespace}/{fieldset.slug}" for fieldset in fieldsets)
    return FieldsetSelectionDTO(presence="explicit", identities=identities)


def native_persistence_fields(owner: Asset | AssetType, fields: Sequence[str]) -> tuple[str, ...]:
    """Keep native save side effects without rewriting command-owned values."""
    names = set(fields)
    if not names:
        return ()
    names.add("updated_at")
    if isinstance(owner, Asset) and "status" in names:
        names.update(("disposed_at", "disposal_value"))
    return tuple(sorted(names))


def current_specification_plan(
    owner: Asset | AssetType,
    *,
    target_kind: str,
    asset_type_id: int | None = None,
) -> SpecificationPlan:
    """Load real revisions from the current owner and effective definition.

    Asset commands validate the effective definition of their destination Type,
    which may differ from the Type currently stored on the owner during a
    switch.  Callers that do not provide a destination retain the current-owner
    behavior used by Type and ordinary Asset value edits.
    """
    stored = stored_values_for(owner)
    definition, _definitions = load_effective_definition(
        asset_type_id if asset_type_id is not None else (owner.asset_type_id if isinstance(owner, Asset) else owner.pk),
        target_kind,
        tuple(stored),
    )
    return SpecificationPlan(
        resource_revision=resource_revision_for_owner(owner),
        definition_revision=DefinitionRevision(definition.revision),
    )


def native_asset_type_create_input(
    values: Mapping[str, object],
    *,
    staged_image_id: str | None = None,
) -> AssetTypeNativeCreateInputDTO:
    """Construct the command's immutable native Type-create DTO."""
    manufacturer = values["manufacturer"]
    category = values.get("category")
    asset_role = values.get("asset_role")
    depreciation = values.get("depreciation")
    tags = values.get("tags") or ()
    return AssetTypeNativeCreateInputDTO(
        manufacturer_id=int(manufacturer.pk),
        model=str(values["model"]),
        slug=values.get("slug") or None,
        part_number=str(values.get("part_number") or ""),
        ean=str(values.get("ean") or ""),
        region=str(values.get("region") or ""),
        configuration=str(values.get("configuration") or ""),
        eol_months=values.get("eol_months"),
        category_id=int(category.pk) if category is not None else None,
        suggested_asset_role_id=int(asset_role.pk) if asset_role is not None else None,
        depreciation_id=int(depreciation.pk) if depreciation is not None else None,
        staged_image_id=StagedImageId(staged_image_id) if staged_image_id is not None else None,
        description=str(values.get("description") or ""),
        comments=str(values.get("comments") or ""),
        tag_ids=tuple(int(tag.pk) for tag in tags),
        requestable=bool(values.get("requestable", False)),
    )


def command_rejection_message(result: object) -> str:
    if isinstance(result, CommandRejectedDTO):
        messages = [issue.message_key for issue in result.issues]
        return "; ".join(messages) or "The specification command was rejected."
    return "The specification command returned an unsupported result."


def require_command_success(result: object) -> object:
    """Raise a presentation error for a rejected canonical command result."""
    if isinstance(result, CommandRejectedDTO):
        raise ValidationError(command_rejection_message(result))
    return result


def owner_id_from_result(result: object) -> int:
    result = require_command_success(result)
    owner = getattr(result, "owner", None)
    if owner is None:
        raise ValidationError("The specification command did not return an owner.")
    return int(owner.owner_id)


def stage_uploaded_image(*, actor: ActorContextDTO, uploaded: object) -> str:
    """Ingest a validated upload before the Type-create command."""
    if uploaded is None:
        raise ValueError("uploaded image is required")
    if hasattr(uploaded, "seek"):
        uploaded.seek(0)
    content = uploaded.read()
    if hasattr(uploaded, "seek"):
        uploaded.seek(0)
    return ingest_staged_image(
        actor=actor,
        command_kind=CREATE_COMMAND_KIND,
        content=content,
        original_name=str(getattr(uploaded, "name", "upload")),
    )


def discard_staged_image(*, stage_id: str, actor: ActorContextDTO) -> None:
    discard_stage(stage_id, actor, CREATE_COMMAND_KIND)


__all__ = [
    "SpecificationPlan",
    "actor_context_for_user",
    "authorization_for_asset",
    "command_rejection_message",
    "create_fieldset_selection",
    "current_specification_plan",
    "discard_staged_image",
    "fieldset_selection",
    "native_asset_type_create_input",
    "owner_id_from_result",
    "patch_from_mapping",
    "require_command_success",
    "specification_patch",
    "stage_uploaded_image",
]
