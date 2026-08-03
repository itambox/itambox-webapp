"""Organization service compatibility exports and container visibility helpers."""

from django.db.models import Q

from ..access import (
    DENIED_INSUFFICIENT_LEVEL,
    DENIED_INVALID_ACCESS_LEVEL,
    DENIED_NO_ACTIVE_TENANT,
    DENIED_NO_GRANT,
    DENIED_OWNER_UNRESOLVABLE,
    DENIED_RBAC,
    DENIED_UNSUPPORTED_RESOURCE,
    REASON_DIRECT_GRANT,
    REASON_GROUP_GRANT,
    REASON_SAME_TENANT,
    ResourceAccessDecision,
    accessible_tenant_ids,
    resolve_stock_access,
    resolved_shared_stock_ids,
)
from ..models import Tenant

# Access-control models whose default manager is deliberately unscoped (their
# tenant resolution is itself an input to tenant scoping). Generic,
# model-agnostic code must apply ``visible_to_containers`` to these instead.
_UNFILTERED_CONTAINER_MODELS = {
    ("organization", "membership"),
    ("organization", "rolegrant"),
    ("organization", "tenantresourcegrant"),
    ("users", "token"),
}


def visible_to_containers(user, qs, perm):
    """Restrict unscoped tenant-anchored rows to authorized containers."""
    if user.is_superuser:
        return qs
    candidate_ids = accessible_tenant_ids(user)
    allowed = [
        tenant.pk
        for tenant in Tenant._base_manager.filter(
            pk__in=candidate_ids,
            deleted_at__isnull=True,
        )
        if user.has_perm(perm, obj=tenant)
    ]
    model = qs.model
    field_names = {field.name for field in model._meta.get_fields()}
    if "tenant" in field_names:
        return qs.filter(tenant_id__in=allowed)
    if {"membership", "user_group"} <= field_names:
        return qs.filter(Q(membership__tenant_id__in=allowed) | Q(user_group__tenant_id__in=allowed))
    if "membership" in field_names:
        return qs.filter(membership__tenant_id__in=allowed)
    return qs.none()


def is_container_scoped_unfiltered(model):
    """Whether generic code must use ``visible_to_containers`` for ``model``."""
    return (model._meta.app_label, model._meta.model_name) in _UNFILTERED_CONTAINER_MODELS


__all__ = [
    "DENIED_INVALID_ACCESS_LEVEL",
    "DENIED_INSUFFICIENT_LEVEL",
    "DENIED_NO_ACTIVE_TENANT",
    "DENIED_NO_GRANT",
    "DENIED_OWNER_UNRESOLVABLE",
    "DENIED_RBAC",
    "DENIED_UNSUPPORTED_RESOURCE",
    "REASON_DIRECT_GRANT",
    "REASON_GROUP_GRANT",
    "REASON_SAME_TENANT",
    "ResourceAccessDecision",
    "is_container_scoped_unfiltered",
    "resolve_stock_access",
    "resolved_shared_stock_ids",
    "visible_to_containers",
]
