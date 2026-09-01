from dataclasses import dataclass, replace

from extras.models import CustomField


@dataclass(frozen=True)
class ResolvedCustomField:
    definition: CustomField
    provenance: tuple[str, ...]
    read_only: bool = False


def _qualified_identity(fieldset):
    return f"{fieldset.namespace}/{fieldset.slug}"


def _resolve_composed_fields(asset_type, scopes, stored_values):
    resolved = []
    by_key = {}
    memberships = asset_type.fieldset_memberships.select_related("fieldset").order_by("position", "fieldset__slug")
    for composition in memberships:
        identity = _qualified_identity(composition.fieldset)
        field_memberships = composition.fieldset.field_memberships.select_related("custom_field").order_by(
            "position", "custom_field__name"
        )
        for membership in field_memberships:
            definition = membership.custom_field
            if definition.scope not in scopes:
                continue
            if definition.lifecycle == CustomField.LIFECYCLE_DELETED:
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


def resolve_asset_type_custom_fields(asset_type):
    stored_values = dict(asset_type.custom_field_data or {})
    return _resolve_composed_fields(
        asset_type,
        {CustomField.SCOPE_ASSET_TYPE, CustomField.SCOPE_BOTH},
        stored_values,
    )


def resolve_asset_custom_fields(asset_type, stored_values=None):
    stored_values = dict(stored_values or {})
    resolved = _resolve_composed_fields(
        asset_type,
        {CustomField.SCOPE_ASSET, CustomField.SCOPE_BOTH},
        stored_values,
    )
    seen = {item.definition.name for item in resolved}
    global_fields = CustomField.objects.filter(
        scope__in=(CustomField.SCOPE_ASSET, CustomField.SCOPE_BOTH),
        fieldset_memberships__isnull=True,
    ).order_by("name")
    for definition in global_fields:
        if definition.name in seen or definition.lifecycle == CustomField.LIFECYCLE_DELETED:
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
