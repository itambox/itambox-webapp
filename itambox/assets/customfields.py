from dataclasses import dataclass, replace

from django.db.models import Q

from extras.models import CustomField, CustomFieldset


@dataclass(frozen=True)
class ResolvedCustomField:
    definition: CustomField
    provenance: tuple[str, ...]
    read_only: bool = False


def _qualified_identity(fieldset):
    return f"{fieldset.namespace}/{fieldset.slug}"


def _definition_applies_to_target(definition, target_model, scopes):
    object_types = getattr(definition, "_prefetched_objects_cache", {}).get("object_types")
    if object_types is None:
        object_types = definition.object_types.all()
    object_types = list(object_types)
    if object_types:
        return any(
            content_type.app_label == "assets" and content_type.model == target_model for content_type in object_types
        )
    return definition.scope in scopes


def _iter_fieldset_memberships(fieldset):
    field_memberships = getattr(fieldset, "_prefetched_objects_cache", {}).get("field_memberships")
    if field_memberships is None:
        return fieldset.field_memberships.select_related("custom_field").order_by("position", "custom_field__name")
    return sorted(
        field_memberships,
        key=lambda membership: (membership.position, membership.custom_field.name),
    )


def _definition_is_resolvable(definition, target_model, scopes, stored_values):
    applies = (
        definition.scope in scopes
        if target_model is None
        else _definition_applies_to_target(definition, target_model, scopes)
    )
    return (
        applies
        and definition.deleted_at is None
        and not (definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED and definition.name not in stored_values)
    )


def _merge_resolved_field(resolved, by_key, definition, identity, target_model, scopes, stored_values):
    if not _definition_is_resolvable(definition, target_model, scopes, stored_values):
        return
    if definition.name in by_key:
        index = by_key[definition.name]
        current = resolved[index]
        resolved[index] = replace(current, provenance=(*current.provenance, identity))
        return
    by_key[definition.name] = len(resolved)
    resolved.append(
        ResolvedCustomField(
            definition=definition,
            provenance=(identity,),
            read_only=definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED,
        )
    )


def resolve_fieldsets_custom_fields(fieldsets, scopes, stored_values=None, target_model=None):
    """Resolve an already ordered fieldset sequence for a domain form or object."""
    stored_values = dict(stored_values or {})
    resolved = []
    by_key = {}
    for fieldset in fieldsets:
        if fieldset.deleted_at is not None or fieldset.lifecycle != CustomFieldset.LIFECYCLE_ACTIVE:
            continue
        identity = _qualified_identity(fieldset)
        for membership in _iter_fieldset_memberships(fieldset):
            _merge_resolved_field(
                resolved,
                by_key,
                membership.custom_field,
                identity,
                target_model,
                scopes,
                stored_values,
            )
    return resolved


def resolve_effective_custom_fields(fieldsets, target_model, scopes, stored_values=None):
    """Resolve composed fields followed by applicable unbound global fields."""
    stored_values = dict(stored_values or {})
    resolved = resolve_fieldsets_custom_fields(fieldsets, scopes, stored_values, target_model=target_model)
    seen = {item.definition.name for item in resolved}
    global_fields = (
        CustomField.objects.filter(
            fieldset_memberships__isnull=True,
            deleted_at__isnull=True,
        )
        .filter(
            Q(object_types__app_label="assets", object_types__model=target_model)
            | Q(object_types__isnull=True, scope__in=scopes)
        )
        .distinct()
        .order_by("name")
    )
    for definition in global_fields:
        if definition.name in seen:
            continue
        if definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED and definition.name not in stored_values:
            continue
        resolved.append(
            ResolvedCustomField(
                definition=definition,
                provenance=(),
                read_only=definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED,
            )
        )
        seen.add(definition.name)
    return resolved


def _composed_fieldsets(asset_type):
    memberships = getattr(asset_type, "_prefetched_objects_cache", {}).get("fieldset_memberships")
    if memberships is None:
        memberships = asset_type.fieldset_memberships.select_related("fieldset").order_by("position", "fieldset__slug")
    else:
        memberships = sorted(
            memberships,
            key=lambda membership: (membership.position, membership.fieldset.slug or ""),
        )
    return [membership.fieldset for membership in memberships]


def resolve_asset_type_custom_fields(asset_type):
    stored_values = dict(asset_type.custom_field_data or {})
    return resolve_effective_custom_fields(
        _composed_fieldsets(asset_type),
        "assettype",
        {CustomField.SCOPE_ASSET_TYPE, CustomField.SCOPE_BOTH},
        stored_values,
    )


def resolve_asset_type_creation_custom_fields():
    """Return global Asset Type fields valid before a fieldset composition exists."""
    return resolve_effective_custom_fields(
        (),
        "assettype",
        {CustomField.SCOPE_ASSET_TYPE, CustomField.SCOPE_BOTH},
    )


def resolve_asset_custom_fields(asset_type, stored_values=None):
    stored_values = dict(stored_values or {})
    fieldsets = _composed_fieldsets(asset_type) if asset_type is not None else ()
    return resolve_effective_custom_fields(
        fieldsets,
        "asset",
        {CustomField.SCOPE_ASSET, CustomField.SCOPE_BOTH},
        stored_values,
    )
