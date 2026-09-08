"""Audited persistence seams for supported non-UI asset writers."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from assets.models import Asset
from assets.services.specifications._command_support import save_owner_in_savepoint
from assets.services.specifications.commands import update_asset_specifications
from assets.services.specifications.contracts import (
    DestinationAssetTypeSelectionDTO,
    SpecificationPatchDTO,
)
from assets.specification_adapters import (
    authorization_for_asset,
    current_specification_plan,
    require_command_success,
)
from organization.access import authorize_tenant_operation
from organization.models import Tenant


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in {bool, int, str}:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return False


def normalize_generic_asset_data(
    updates: Mapping[str, object],
    *,
    allowed_keys: Collection[str],
) -> dict[str, object]:
    """Validate one explicit generic CustomFieldData update before persistence."""
    if not isinstance(updates, Mapping):
        raise ValidationError("Generic asset data must be a mapping of supported keys.")
    allowed = frozenset(allowed_keys)
    normalized = dict(updates)
    for key, value in normalized.items():
        if type(key) is not str or not key:
            raise ValidationError("Generic asset data keys must be non-empty strings.")
        if key not in allowed:
            raise ValidationError(f"unsupported generic asset data key: {key}")
        if not _is_json_value(value):
            raise ValidationError("Generic asset data values must be JSON values.")
    return normalized


def merge_generic_asset_data(
    *,
    asset_id: int,
    user: object,
    updates: Mapping[str, object],
    allowed_keys: Collection[str],
) -> Asset:
    """Merge supported generic data under a row lock and current actor scope."""
    normalized = normalize_generic_asset_data(updates, allowed_keys=allowed_keys)
    if type(asset_id) is not int or asset_id <= 0:
        raise ValidationError("A positive Asset ID is required.")

    with transaction.atomic():
        asset = Asset._base_manager.select_for_update().filter(pk=asset_id, deleted_at__isnull=True).first()
        if asset is None:
            raise ValidationError("The Asset no longer exists.")
        if asset.tenant_id is None:
            raise PermissionDenied("Generic Asset data requires a tenant-owned Asset.")
        authorization_for_asset(user=user, tenant_id=asset.tenant_id)
        current = dict(asset.custom_field_data or {})
        proposed = {**current, **normalized}
        if proposed == current:
            return asset
        asset.custom_field_data = proposed
        save_owner_in_savepoint(
            asset,
            user,
            update_fields=("custom_field_data", "updated_at"),
        )
        return asset


def authorize_generic_owner_scope(
    *,
    user: object,
    owner_model: type[object],
    tenant_id: int | None,
    allow_global: bool = False,
) -> None:
    """Require live actor permission for a tenant-owned or explicit global write."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise PermissionDenied("Generic owner writes require an authenticated actor.")
    permission = f"{owner_model._meta.app_label}.change_{owner_model._meta.model_name}"
    if tenant_id is None:
        if not allow_global or not user.has_perm(permission):
            raise PermissionDenied("Generic owner data requires an authorized tenant or global owner.")
        return
    tenant = Tenant._base_manager.filter(pk=tenant_id, deleted_at__isnull=True).first()
    if tenant is None or not authorize_tenant_operation(user, tenant, permission):
        raise PermissionDenied("The actor is not authorized for this generic owner.")


def _lock_and_authorize_generic_owner(*, owner, user: object):
    owner_query = owner.__class__._base_manager.select_for_update().filter(pk=owner.pk)
    if any(field.name == "deleted_at" for field in owner._meta.concrete_fields):
        owner_query = owner_query.filter(deleted_at__isnull=True)
    locked = owner_query.first()
    if locked is None:
        raise ValidationError("The generic owner no longer exists.")
    authorize_generic_owner_scope(
        user=user,
        owner_model=locked.__class__,
        tenant_id=getattr(locked, "tenant_id", None),
        allow_global=bool(
            getattr(locked.__class__, "changelog_global", False)
            or getattr(locked.__class__, "allow_global_tenant", False)
        ),
    )
    return locked


def merge_generic_owner_data(
    *,
    owner,
    user: object,
    updates: Mapping[str, object],
    allowed_keys: Collection[str],
):
    """Persist explicit generic payload after live owner-scope authorization."""
    normalized = normalize_generic_asset_data(updates, allowed_keys=allowed_keys)
    if not getattr(owner, "pk", None):
        raise ValidationError("Generic owner must have a persisted primary key.")

    with transaction.atomic():
        locked = _lock_and_authorize_generic_owner(owner=owner, user=user)
        current = dict(locked.custom_field_data or {})
        merged = {**current, **normalized}
        if merged == current:
            return locked
        locked.custom_field_data = merged
        save_owner_in_savepoint(locked, user, update_fields=("custom_field_data", "updated_at"))
        return locked


def apply_asset_specification_patch(
    *,
    asset_id: int,
    user: object,
    set_values: Mapping[str, object],
    asset_type_id: int | None = None,
) -> object:
    """Apply mapped writer values through the canonical revision/auth command."""
    asset = Asset._base_manager.filter(pk=asset_id, deleted_at__isnull=True).first()
    if asset is None:
        raise ValidationError("The Asset no longer exists.")
    authorization = authorization_for_asset(user=user, tenant_id=asset.tenant_id)
    plan = current_specification_plan(
        asset,
        target_kind="asset",
        asset_type_id=asset_type_id,
    )
    destination = (
        DestinationAssetTypeSelectionDTO("replace", asset_type_id)
        if asset_type_id is not None
        else DestinationAssetTypeSelectionDTO("keep_current", None)
    )
    result = update_asset_specifications(
        authorization=authorization,
        asset_id=asset_id,
        destination=destination,
        expected_resource_revision=plan.resource_revision,
        expected_definition_revision=plan.definition_revision,
        patch=SpecificationPatchDTO(set_values=dict(set_values), clear_keys=()),
    )
    return require_command_success(result)


__all__ = [
    "apply_asset_specification_patch",
    "authorize_generic_owner_scope",
    "merge_generic_asset_data",
    "merge_generic_owner_data",
    "normalize_generic_asset_data",
]
