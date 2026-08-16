"""Organization service compatibility exports and container visibility helpers."""

from typing import Any, Protocol, TypeVar

from django.db.models import Model, Q, QuerySet

from core.managers import (
    get_current_all_accessible,
    get_current_scope_conflict,
    get_current_tenant,
    get_current_tenant_group,
)

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
    get_ancestor_tenant_group_ids,
    get_descendant_tenant_group_ids,
    resolve_stock_access,
    resolved_shared_stock_ids,
)
from ..models import Tenant

_ModelT = TypeVar("_ModelT", bound=Model)


class _ContainerPermissionActor(Protocol):
    is_superuser: bool

    def has_perm(self, perm: str, obj: object | None = None) -> bool:
        pass


# Access-control models whose default manager is deliberately unscoped (their
# tenant resolution is itself an input to tenant scoping). Generic,
# model-agnostic code must apply ``visible_to_containers`` to these instead.
_UNFILTERED_CONTAINER_MODELS = {
    ("organization", "membership"),
    ("organization", "rolegrant"),
    ("organization", "tenantresourcegrant"),
    ("users", "token"),
}


def _resource_grant_container_ids(  # noqa: C901
    user: _ContainerPermissionActor,
    perm: str,
    *,
    request: Any | None = None,
) -> set[int] | None:
    """Return the request-bound container set, or ``None`` for platform-global."""

    if not getattr(user, "is_authenticated", False):
        return set()
    if get_current_scope_conflict(user):
        return set()

    active_tenant = get_current_tenant()
    active_group = get_current_tenant_group()
    all_accessible = get_current_all_accessible()
    token_tenant_id = getattr(getattr(request, "auth", None), "tenant_id", None)

    if token_tenant_id is not None:
        if active_tenant is None or active_tenant.pk != token_tenant_id:
            return set()
        candidate_ids = {token_tenant_id}
    elif active_tenant is not None:
        candidate_ids = {active_tenant.pk}
    elif active_group is not None:
        group_ids = get_descendant_tenant_group_ids(active_group.pk, live_only=True)
        candidate_ids = set(
            Tenant._base_manager.filter(
                group_id__in=group_ids,
                deleted_at__isnull=True,
            ).values_list("pk", flat=True)
        )
        if not user.is_superuser:
            candidate_ids &= set(accessible_tenant_ids(user))
    elif all_accessible:
        candidate_ids = set(accessible_tenant_ids(user))
    elif user.is_superuser:
        # Only a genuinely unbound platform superuser receives the explicit
        # global audit scope. Other superuser scopes were handled above.
        return None
    else:
        return set()

    live_tenants = Tenant._base_manager.filter(pk__in=candidate_ids, deleted_at__isnull=True)
    if user.is_superuser:
        return set(live_tenants.values_list("pk", flat=True))
    return {tenant.pk for tenant in live_tenants if user.has_perm(perm, obj=tenant)}


def visible_to_containers(
    user: _ContainerPermissionActor,
    qs: QuerySet[_ModelT],
    perm: str,
    *,
    request: Any | None = None,  # typing: third-party-untyped: DRF request objects are unparameterized
) -> QuerySet[_ModelT]:
    """Restrict unscoped tenant-anchored rows to authorized containers."""

    model = qs.model
    if model._meta.label_lower == "organization.tenantresourcegrant":
        container_ids = _resource_grant_container_ids(user, perm, request=request)
        if container_ids is None:
            return qs
        if not container_ids:
            return qs.none()
        group_ids = set(
            Tenant._base_manager.filter(
                pk__in=container_ids,
                deleted_at__isnull=True,
            )
            .exclude(group_id__isnull=True)
            .values_list("group_id", flat=True)
        )
        visible_group_ids = set()
        for group_id in group_ids:
            visible_group_ids |= get_ancestor_tenant_group_ids(group_id, live_only=True)
        return qs.filter(
            Q(tenant_id__in=container_ids)
            | Q(grantee_tenant_id__in=container_ids)
            | Q(grantee_tenant_group_id__in=visible_group_ids)
        ).distinct()

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
    field_names = {field.name for field in model._meta.get_fields()}
    if "tenant" in field_names:
        return qs.filter(tenant_id__in=allowed)
    if {"membership", "user_group"} <= field_names:
        return qs.filter(Q(membership__tenant_id__in=allowed) | Q(user_group__tenant_id__in=allowed))
    if "membership" in field_names:
        return qs.filter(membership__tenant_id__in=allowed)
    return qs.none()


def is_container_scoped_unfiltered(model: type[Model]) -> bool:
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
