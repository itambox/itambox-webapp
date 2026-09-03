from dataclasses import dataclass, replace

from extras.models import CustomField, CustomFieldset


@dataclass(frozen=True)
class ResolvedCustomField:
    definition: CustomField
    provenance: tuple[str, ...]
    read_only: bool = False


def _qualified_identity(fieldset):
    return f"{fieldset.namespace}/{fieldset.slug}"


def resolve_fieldsets_custom_fields(fieldsets, scopes, stored_values=None):
    """Resolve an already ordered fieldset sequence for a domain form or object."""
    stored_values = dict(stored_values or {})
    resolved = []
    by_key = {}
    for fieldset in fieldsets:
        if fieldset.deleted_at is not None or fieldset.lifecycle != CustomFieldset.LIFECYCLE_ACTIVE:
            continue
        identity = _qualified_identity(fieldset)
        field_memberships = getattr(fieldset, "_prefetched_objects_cache", {}).get("field_memberships")
        if field_memberships is None:
            field_memberships = fieldset.field_memberships.select_related("custom_field").order_by(
                "position", "custom_field__name"
            )
        else:
            field_memberships = sorted(
                field_memberships,
                key=lambda membership: (membership.position, membership.custom_field.name),
            )
        for membership in field_memberships:
            definition = membership.custom_field
            if definition.scope not in scopes:
                continue
            if definition.deleted_at is not None:
                continue
            if definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED and definition.name not in stored_values:
                continue
            if definition.name in by_key:
                index = by_key[definition.name]
                current = resolved[index]
                resolved[index] = replace(current, provenance=(*current.provenance, identity))
                continue
            by_key[definition.name] = len(resolved)
            resolved.append(
                ResolvedCustomField(
                    definition=definition,
                    provenance=(identity,),
                    read_only=definition.lifecycle == CustomField.LIFECYCLE_DEPRECATED,
                )
            )
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
    return resolve_fieldsets_custom_fields(
        _composed_fieldsets(asset_type),
        {CustomField.SCOPE_ASSET_TYPE, CustomField.SCOPE_BOTH},
        stored_values,
    )


def resolve_asset_type_creation_custom_fields():
    """Return only global Asset Type fields valid before a fieldset composition exists."""
    return CustomField.objects.filter(
        object_types__app_label="assets",
        object_types__model="assettype",
        fieldset_memberships__isnull=True,
        lifecycle=CustomField.LIFECYCLE_ACTIVE,
        deleted_at__isnull=True,
    ).distinct()


def resolve_asset_custom_fields(asset_type, stored_values=None):
    stored_values = dict(stored_values or {})
    resolved = []
    if asset_type is not None:
        resolved = resolve_fieldsets_custom_fields(
            _composed_fieldsets(asset_type),
            {CustomField.SCOPE_ASSET, CustomField.SCOPE_BOTH},
            stored_values,
        )
    seen = {item.definition.name for item in resolved}
    global_fields = CustomField.objects.filter(
        scope__in=(CustomField.SCOPE_ASSET, CustomField.SCOPE_BOTH),
        fieldset_memberships__isnull=True,
    ).order_by("name")
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
    return resolved
