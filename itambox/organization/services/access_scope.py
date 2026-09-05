"""Organization-owned authorization authority for explicit tenant scopes.

The selector in this module is untrusted request syntax.  Every successful
``AccessScopeDTO`` is derived from a freshly reloaded actor, the registered
Organization/Core authorization providers, and live tenant topology.  No
ambient tenant context or staff/superuser flag is an authorization source.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Literal, NewType, TypeAlias

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from core import tenant_access as _tenant_access
from core import tenant_scope as _tenant_scope
from organization.models import Membership, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from users.models import GroupMembership, UserGroup

logger = logging.getLogger(__name__)

ActorId = NewType("ActorId", int)
TenantId = NewType("TenantId", int)
TenantGroupId = NewType("TenantGroupId", int)
AuthenticationRevision = NewType("AuthenticationRevision", str)
SelectorFingerprint = NewType("SelectorFingerprint", str)
AuthorizationRevision = NewType("AuthorizationRevision", str)
AccessScopeFingerprint = NewType("AccessScopeFingerprint", str)

ScopeMode: TypeAlias = Literal["tenant", "tenant_group", "all_accessible"]
ScopeOperation: TypeAlias = Literal[
    "read_asset",
    "update_asset_specifications",
    "cleanup_asset_specification_history",
]

_SCOPE_MODES = frozenset({"tenant", "tenant_group", "all_accessible"})
_SCOPE_OPERATIONS = frozenset(
    {
        "read_asset",
        "update_asset_specifications",
        "cleanup_asset_specification_history",
    }
)


@dataclass(frozen=True)
class ActorContextDTO:
    actor_id: ActorId
    authentication_revision: AuthenticationRevision

    def __post_init__(self) -> None:
        _require_positive_id(self.actor_id, "actor_id")
        if not isinstance(self.authentication_revision, str) or not self.authentication_revision:
            raise ValueError("authentication_revision must be a non-empty string")


@dataclass(frozen=True)
class RequestedScopeSelectorDTO:
    mode: ScopeMode
    tenant_id: TenantId | None
    tenant_group_id: TenantGroupId | None

    def __post_init__(self) -> None:
        if self.mode not in _SCOPE_MODES:
            raise ValueError(f"unsupported scope mode: {self.mode!r}")
        if self.tenant_id is not None:
            _require_positive_id(self.tenant_id, "tenant_id")
        if self.tenant_group_id is not None:
            _require_positive_id(self.tenant_group_id, "tenant_group_id")

        expected = {
            "tenant": (self.tenant_id is not None and self.tenant_group_id is None),
            "tenant_group": (self.tenant_id is None and self.tenant_group_id is not None),
            "all_accessible": (self.tenant_id is None and self.tenant_group_id is None),
        }
        if not expected[self.mode]:
            raise ValueError(f"selector ids do not match scope mode {self.mode!r}")


@dataclass(frozen=True)
class AccessScopeResolutionRequestDTO:
    actor: ActorContextDTO
    selector: RequestedScopeSelectorDTO
    operation: ScopeOperation
    required_permission: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorContextDTO):
            raise TypeError("actor must be an ActorContextDTO")
        if not isinstance(self.selector, RequestedScopeSelectorDTO):
            raise TypeError("selector must be a RequestedScopeSelectorDTO")
        if self.operation not in _SCOPE_OPERATIONS:
            raise ValueError(f"unsupported scope operation: {self.operation!r}")
        if not isinstance(self.required_permission, str) or not self.required_permission.strip():
            raise ValueError("required_permission must be a non-empty string")


@dataclass(frozen=True)
class AccessScopeDTO:
    mode: ScopeMode
    authorized_tenant_ids: frozenset[TenantId]
    selector_fingerprint: SelectorFingerprint
    authorization_revision: AuthorizationRevision
    access_scope_fingerprint: AccessScopeFingerprint
    valid_until_epoch_seconds: int | None

    def __post_init__(self) -> None:
        if self.mode not in _SCOPE_MODES:
            raise ValueError(f"unsupported scope mode: {self.mode!r}")
        if not isinstance(self.authorized_tenant_ids, frozenset) or not self.authorized_tenant_ids:
            raise ValueError("authorized_tenant_ids must be a non-empty frozenset")
        for tenant_id in self.authorized_tenant_ids:
            _require_positive_id(tenant_id, "authorized tenant id")
        for name in ("selector_fingerprint", "authorization_revision", "access_scope_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.valid_until_epoch_seconds is not None:
            if not isinstance(self.valid_until_epoch_seconds, int) or isinstance(
                self.valid_until_epoch_seconds, bool
            ):
                raise TypeError("valid_until_epoch_seconds must be an integer or None")
            if self.valid_until_epoch_seconds < 0:
                raise ValueError("valid_until_epoch_seconds must not be negative")


@dataclass(frozen=True)
class AccessScopeResolvedDTO:
    outcome: Literal["resolved"]
    request: AccessScopeResolutionRequestDTO
    access_scope: AccessScopeDTO

    def __post_init__(self) -> None:
        if self.outcome != "resolved":
            raise ValueError("resolved result must have outcome='resolved'")
        if not isinstance(self.request, AccessScopeResolutionRequestDTO):
            raise TypeError("request must be an AccessScopeResolutionRequestDTO")
        if not isinstance(self.access_scope, AccessScopeDTO):
            raise TypeError("access_scope must be an AccessScopeDTO")


@dataclass(frozen=True)
class AccessScopeDeniedDTO:
    outcome: Literal["denied"]
    public_code: Literal["OBJECT_UNAVAILABLE"]
    public_path: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.outcome != "denied":
            raise ValueError("denied result must have outcome='denied'")
        if self.public_code != "OBJECT_UNAVAILABLE":
            raise ValueError("access-scope denial must use OBJECT_UNAVAILABLE")
        if not isinstance(self.public_path, tuple):
            raise TypeError("public_path must be a tuple")


@dataclass(frozen=True)
class ResolvedAccessAuthorizationDTO:
    actor: ActorContextDTO
    request: AccessScopeResolutionRequestDTO
    initial_scope: AccessScopeDTO

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorContextDTO):
            raise TypeError("actor must be an ActorContextDTO")
        if not isinstance(self.request, AccessScopeResolutionRequestDTO):
            raise TypeError("request must be an AccessScopeResolutionRequestDTO")
        if self.actor != self.request.actor:
            raise ValueError("authorization actor must match the original request actor")
        if not isinstance(self.initial_scope, AccessScopeDTO):
            raise TypeError("initial_scope must be an AccessScopeDTO")


AccessScopeResolutionResult: TypeAlias = AccessScopeResolvedDTO | AccessScopeDeniedDTO


def authentication_revision_for_actor(actor: object) -> AuthenticationRevision:
    """Return the server-derived revision used to bind an actor context.

    The current User model has no dedicated revision column.  The revision is
    therefore a versioned digest of authentication state that can invalidate an
    already-issued actor context without exposing password material.  A future
    auth implementation may expose an explicit non-empty revision attribute;
    this helper preserves that server-owned value.
    """
    explicit_revision = getattr(actor, "authentication_revision", None)
    if isinstance(explicit_revision, str) and explicit_revision:
        return AuthenticationRevision(explicit_revision)

    actor_id = getattr(actor, "pk", None)
    _require_positive_id(actor_id, "actor pk")
    payload = {
        "version": 1,
        "actor_id": actor_id,
        "password": str(getattr(actor, "password", "")),
        "is_active": bool(getattr(actor, "is_active", False)),
        "can_login": bool(getattr(actor, "can_login", True)),
        "last_login": _canonical_datetime(getattr(actor, "last_login", None)),
    }
    return AuthenticationRevision(_sha256_json(payload))


def resolve_access_scope(request: AccessScopeResolutionRequestDTO) -> AccessScopeResolutionResult:
    """Resolve one explicit selector against current Organization authorization.

    Every operational or data error returns the same nondisclosing denial.  The
    request DTO itself performs syntax validation before this fail-closed runtime
    boundary is entered.
    """
    if not isinstance(request, AccessScopeResolutionRequestDTO):
        raise TypeError("request must be an AccessScopeResolutionRequestDTO")

    try:
        actor = _reload_authenticated_actor(request.actor)
        if actor is None:
            return _denied()
        accessible_ids, earliest_expiry, permission_map, grants = _read_provider_data(actor)
        live_tenant_ids = _live_tenant_ids()
        _validate_provider_sets(accessible_ids, permission_map, live_tenant_ids)
        authorized_ids = _authorized_tenant_ids(
            request.selector,
            request.required_permission,
            permission_map,
            live_tenant_ids,
        )
        if authorized_ids is None:
            return _denied()
        return _resolved_scope(
            request=request,
            actor=actor,
            accessible_ids=accessible_ids,
            earliest_expiry=earliest_expiry,
            permission_map=permission_map,
            grants=grants,
            authorized_ids=authorized_ids,
        )
    # broad except: boundary-isolation: provider, query, or topology errors fail closed to nondisclosing denial
    except Exception as exc:
        logger.warning(
            "Access-scope resolution failed closed (exception_type=%s)",
            type(exc).__name__,
        )
        return _denied()


def _reload_authenticated_actor(actor_context: ActorContextDTO):
    actor = _reload_actor(actor_context.actor_id)
    if actor is None:
        return None
    if authentication_revision_for_actor(actor) != actor_context.authentication_revision:
        return None
    return actor


def _reload_actor(actor_id: int):
    user_model = get_user_model()
    return user_model._base_manager.filter(pk=actor_id, is_active=True).first()


def _live_tenant_ids() -> set[int]:
    return set(Tenant._base_manager.filter(deleted_at__isnull=True).values_list("pk", flat=True))


def _validate_provider_sets(
    accessible_ids: set[int],
    permission_map: Mapping[int, tuple[frozenset[str], datetime | None]],
    live_tenant_ids: set[int],
) -> None:
    if not accessible_ids.issubset(live_tenant_ids):
        raise ValueError("provider returned a deleted or missing accessible tenant")
    if not set(permission_map).issubset(accessible_ids):
        raise ValueError("permission map is outside the accessible tenant set")


def _authorized_tenant_ids(
    selector: RequestedScopeSelectorDTO,
    required_permission: str,
    permission_map: Mapping[int, tuple[frozenset[str], datetime | None]],
    live_tenant_ids: set[int],
) -> set[int] | None:
    permission_authorized_ids = {
        tenant_id
        for tenant_id, (permissions, _map_expiry) in permission_map.items()
        if required_permission in permissions
    }
    selected_ids = _select_requested_tenants(selector, live_tenant_ids)
    if selected_ids is None:
        return None
    if selector.mode == "all_accessible":
        return permission_authorized_ids or None
    if not selected_ids.issubset(permission_authorized_ids):
        return None
    return selected_ids


def _resolved_scope(
    *,
    request: AccessScopeResolutionRequestDTO,
    actor: object,
    accessible_ids: set[int],
    earliest_expiry: datetime | None,
    permission_map: Mapping[int, tuple[frozenset[str], datetime | None]],
    grants: tuple[RoleGrant, ...],
    authorized_ids: set[int],
) -> AccessScopeResolvedDTO:
    actor_auth_revision = authentication_revision_for_actor(actor)
    authorization_evidence = _authorization_evidence(
        actor=actor,
        actor_auth_revision=actor_auth_revision,
        required_permission=request.required_permission,
        accessible_ids=accessible_ids,
        permission_map=permission_map,
        grants=grants,
        selector=request.selector,
        authorized_ids=authorized_ids,
        earliest_expiry=earliest_expiry,
    )
    authorization_revision = AuthorizationRevision(_sha256_json(authorization_evidence))
    selector_fingerprint = SelectorFingerprint(_selector_fingerprint(request.selector))
    expiry_epoch_seconds = _expiry_epoch_seconds(earliest_expiry)
    access_scope_fingerprint = AccessScopeFingerprint(
        _sha256_json(
            {
                "version": 1,
                "actor_id": request.actor.actor_id,
                "operation": request.operation,
                "required_permission": request.required_permission,
                "selector_fingerprint": selector_fingerprint,
                "authorization_revision": authorization_revision,
                "authorized_tenant_ids": sorted(authorized_ids),
                "valid_until_epoch_seconds": expiry_epoch_seconds,
            }
        )
    )
    access_scope = AccessScopeDTO(
        mode=request.selector.mode,
        authorized_tenant_ids=frozenset(authorized_ids),
        selector_fingerprint=selector_fingerprint,
        authorization_revision=authorization_revision,
        access_scope_fingerprint=access_scope_fingerprint,
        valid_until_epoch_seconds=expiry_epoch_seconds,
    )
    return AccessScopeResolvedDTO(
        outcome="resolved",
        request=request,
        access_scope=access_scope,
    )


def reauthorize_access_scope(
    authorization: ResolvedAccessAuthorizationDTO,
) -> AccessScopeResolutionResult:
    """Replay the original actor/request, never the initial scope as authority."""
    if not isinstance(authorization, ResolvedAccessAuthorizationDTO):
        raise TypeError("authorization must be a ResolvedAccessAuthorizationDTO")
    return resolve_access_scope(authorization.request)


def _read_provider_data(actor: object):
    raw_access = _tenant_scope.accessible_tenant_ids_with_expiry(actor)
    if not isinstance(raw_access, tuple) or len(raw_access) != 2:
        raise ValueError("accessible tenant provider returned malformed data")
    accessible_ids = _positive_ids(
        _tenant_access.accessible_tenant_ids(actor),
        "accessible tenant id",
    )
    expiry_ids = _positive_ids(raw_access[0], "accessible tenant id")
    if expiry_ids != accessible_ids:
        raise ValueError("tenant access providers returned inconsistent data")
    earliest_expiry = _validate_expiry(raw_access[1])

    raw_permission_map = _tenant_scope.build_accessible_tenant_permissions_map(actor)
    permission_map = _normalize_permission_map(raw_permission_map)
    grants = _normalize_grants(_tenant_scope.applicable_grants(actor))
    return accessible_ids, earliest_expiry, permission_map, grants


def _normalize_permission_map(raw_permission_map: object) -> dict[int, tuple[frozenset[str], datetime | None]]:
    if not isinstance(raw_permission_map, Mapping):
        raise ValueError("permission provider returned malformed data")
    return {
        tenant_id: (_normalize_permissions(raw_value[0]), _validate_expiry(raw_value[1]))
        for tenant_id, raw_value in _validated_permission_entries(raw_permission_map)
    }


def _validated_permission_entries(raw_permission_map: Mapping[object, object]):
    for tenant_id, raw_value in raw_permission_map.items():
        _require_positive_id(tenant_id, "permission-map tenant id")
        if not isinstance(raw_value, tuple) or len(raw_value) != 2:
            raise ValueError("permission provider returned a malformed tenant entry")
        yield tenant_id, raw_value


def _normalize_permissions(permissions: object) -> frozenset[str]:
    if not isinstance(permissions, Iterable) or isinstance(permissions, (str, bytes)):
        raise ValueError("permission provider returned malformed permissions")
    values = []
    for permission in permissions:
        if not isinstance(permission, str) or not permission:
            raise ValueError("permission provider returned a malformed permission")
        values.append(permission)
    return frozenset(values)


def _normalize_grants(raw_grants: object) -> tuple[RoleGrant, ...]:
    if not isinstance(raw_grants, Iterable) or isinstance(raw_grants, (str, bytes)):
        raise ValueError("grant provider returned malformed data")
    grants = tuple(raw_grants)
    if any(not isinstance(grant, RoleGrant) or not getattr(grant, "pk", None) for grant in grants):
        raise ValueError("grant provider returned a malformed grant")
    return grants


def _fresh_live_descendant_tenant_group_ids(group_id: int | None) -> set[int]:
    """Read the live group tree without the request-local descendant cache."""
    if group_id is None:
        return set()
    rows = list(
        TenantGroup._base_manager.filter(deleted_at__isnull=True)
        .values("pk", "parent_id")
        .order_by("pk")
    )
    live_ids = {row["pk"] for row in rows}
    if group_id not in live_ids:
        return set()
    children_by_parent: dict[int, list[int]] = {}
    for row in rows:
        parent_id = row["parent_id"]
        if parent_id is not None:
            children_by_parent.setdefault(parent_id, []).append(row["pk"])
    descendants = {group_id}
    frontier = [group_id]
    while frontier:
        children = [child_id for parent_id in frontier for child_id in children_by_parent.get(parent_id, ())]
        children = [child_id for child_id in children if child_id not in descendants]
        descendants.update(children)
        frontier = children
    return descendants


def _select_requested_tenants(
    selector: RequestedScopeSelectorDTO,
    live_tenant_ids: set[int],
) -> set[int] | None:
    if selector.mode == "tenant":
        selected = {selector.tenant_id}
        return selected if selected.issubset(live_tenant_ids) else None
    if selector.mode == "all_accessible":
        return set()

    group_ids = _fresh_live_descendant_tenant_group_ids(selector.tenant_group_id)
    group_ids = _positive_ids(group_ids, "tenant group id")
    if not group_ids:
        return None
    live_group_ids = set(
        TenantGroup._base_manager.filter(
            pk__in=group_ids,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    if live_group_ids != group_ids:
        raise ValueError("tenant-group provider returned inconsistent topology")
    selected_ids = set(
        Tenant._base_manager.filter(
            group_id__in=group_ids,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    return selected_ids if selected_ids else None


def _load_membership_evidence(
    *,
    actor_id: int,
    accessible_ids: set[int],
    membership_ids: set[int],
) -> list[dict[str, object]]:
    rows = list(
        Membership._base_manager.filter(
            user_id=actor_id,
            tenant__deleted_at__isnull=True,
        )
        .filter(
            Q(pk__in=membership_ids)
            | Q(is_active=True, tenant_id__in=accessible_ids),
        )
        .values("pk", "user_id", "tenant_id", "is_active")
        .order_by("pk")
    )
    loaded_ids = {row["pk"] for row in rows}
    if not membership_ids.issubset(loaded_ids):
        raise ValueError("authorization evidence references a missing membership")
    return rows


def _authorization_evidence(
    *,
    actor: object,
    actor_auth_revision: str,
    required_permission: str,
    accessible_ids: set[int],
    permission_map: Mapping[int, tuple[frozenset[str], datetime | None]],
    grants: tuple[RoleGrant, ...],
    selector: RequestedScopeSelectorDTO,
    authorized_ids: set[int],
    earliest_expiry: datetime | None,
) -> dict[str, object]:
    grant_ids = {grant.pk for grant in grants}
    if len(grant_ids) != len(grants):
        raise ValueError("grant provider returned duplicate grant identities")

    grant_rows = list(
        RoleGrant._base_manager.filter(pk__in=grant_ids)
        .values(
            "pk",
            "membership_id",
            "user_group_id",
            "role_id",
            "role__tenant_id",
            "role__permissions",
            "role__shared_with_managed",
            "role__deleted_at",
            "valid_until",
        )
        .order_by("pk")
    )
    if len(grant_rows) != len(grant_ids):
        raise ValueError("grant provider returned a stale grant")
    scope_rows = list(
        RoleGrantScope._base_manager.filter(role_grant_id__in=grant_ids)
        .values("pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id")
        .order_by("pk")
    )
    group_ids = {row["user_group_id"] for row in grant_rows if row["user_group_id"] is not None}
    user_group_rows = list(
        UserGroup._base_manager.filter(pk__in=group_ids)
        .values("pk", "tenant_id", "is_active", "deleted_at")
        .order_by("pk")
    )
    if len(user_group_rows) != len(group_ids):
        raise ValueError("grant provider returned a stale user group")
    group_membership_rows = list(
        GroupMembership._base_manager.filter(
            user_group_id__in=group_ids,
            membership__user_id=actor.pk,
        )
        .values(
            "pk",
            "user_group_id",
            "membership_id",
            "membership__tenant_id",
            "membership__is_active",
        )
        .order_by("pk")
    )

    membership_ids = {
        row["membership_id"]
        for row in grant_rows
        if row["membership_id"] is not None
    }
    membership_ids.update(row["membership_id"] for row in group_membership_rows)
    membership_rows = _load_membership_evidence(
        actor_id=actor.pk,
        accessible_ids=accessible_ids,
        membership_ids=membership_ids,
    )

    relevant_tenant_ids = set(accessible_ids) | set(permission_map) | set(authorized_ids)
    relevant_tenant_ids.update(row["tenant_id"] for row in membership_rows)
    relevant_tenant_ids.update(row["tenant_id"] for row in user_group_rows)
    relevant_tenant_ids.update(row["tenant_id"] for row in scope_rows if row["tenant_id"] is not None)
    relevant_tenant_ids.update(
        row["role__tenant_id"] for row in grant_rows if row["role__tenant_id"] is not None
    )
    tenant_rows = list(
        Tenant._base_manager.filter(
            pk__in=relevant_tenant_ids,
            deleted_at__isnull=True,
        )
        .values("pk", "group_id", "managed_by_id", "is_provider")
        .order_by("pk")
    )
    live_relevant_ids = {row["pk"] for row in tenant_rows}
    if not relevant_tenant_ids.issubset(live_relevant_ids):
        # A stale membership/grant reference is authorization evidence failure,
        # not a reason to silently omit the reference from the revision.
        raise ValueError("authorization evidence references a missing tenant")

    relevant_group_ids = {row["group_id"] for row in tenant_rows if row["group_id"] is not None}
    relevant_group_ids.update(row["tenant_group_id"] for row in scope_rows if row["tenant_group_id"] is not None)
    if selector.tenant_group_id is not None:
        relevant_group_ids.add(selector.tenant_group_id)
    tenant_group_rows = _load_group_topology(relevant_group_ids)

    return {
        "version": 1,
        "actor": {
            "id": actor.pk,
            "authentication_revision": actor_auth_revision,
        },
        "required_permission": required_permission,
        "accessible_tenant_ids": sorted(accessible_ids),
        "permission_map": [
            {
                "tenant_id": tenant_id,
                "permissions": sorted(permissions),
                "valid_until": _canonical_datetime(map_expiry),
            }
            for tenant_id, (permissions, map_expiry) in sorted(permission_map.items())
        ],
        "earliest_expiry": _canonical_datetime(earliest_expiry),
        "memberships": _canonical_rows(membership_rows),
        "role_grants": _canonical_rows(grant_rows),
        "role_grant_scopes": _canonical_rows(scope_rows),
        "user_groups": _canonical_rows(user_group_rows),
        "group_memberships": _canonical_rows(group_membership_rows),
        "tenant_topology": _canonical_rows(tenant_rows),
        "tenant_group_topology": _canonical_rows(tenant_group_rows),
    }


def _load_group_topology(group_ids: set[int]) -> list[dict[str, object]]:
    if not group_ids:
        return []
    rows = list(
        TenantGroup._base_manager.filter(deleted_at__isnull=True)
        .values("pk", "parent_id")
        .order_by("pk")
    )
    by_id = {row["pk"]: row for row in rows}
    expanded_ids = set(group_ids)
    for group_id in tuple(group_ids):
        current = group_id
        visited = set()
        while current is not None:
            if current in visited:
                raise ValueError("tenant-group topology contains a cycle")
            visited.add(current)
            row = by_id.get(current)
            if row is None:
                raise ValueError("authorization evidence references a missing tenant group")
            expanded_ids.add(current)
            current = row["parent_id"]
    return [by_id[group_id] for group_id in sorted(expanded_ids)]


def _selector_fingerprint(selector: RequestedScopeSelectorDTO) -> str:
    return _sha256_json(
        {
            "version": 1,
            "mode": selector.mode,
            "selected_id": selector.tenant_id if selector.mode == "tenant" else selector.tenant_group_id,
        }
    )


def _denied() -> AccessScopeDeniedDTO:
    return AccessScopeDeniedDTO(
        outcome="denied",
        public_code="OBJECT_UNAVAILABLE",
        public_path=(),
    )


def _require_positive_id(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _positive_ids(values: object, name: str) -> set[int]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} collection is malformed")
    result = set()
    for value in values:
        _require_positive_id(value, name)
        result.add(value)
    return result


def _validate_expiry(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("expiry must be a datetime or None")
    return value


def _expiry_epoch_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    normalized = value
    if timezone.is_naive(normalized):
        normalized = timezone.make_aware(normalized, datetime_timezone.utc)
    return int(normalized.timestamp())


def _canonical_datetime(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("authorization evidence contains a non-datetime timestamp")
    normalized = value
    if timezone.is_naive(normalized):
        normalized = timezone.make_aware(normalized, datetime_timezone.utc)
    return normalized.astimezone(datetime_timezone.utc).isoformat(timespec="microseconds")


def _canonical_rows(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        normalized = {}
        for key in sorted(row):
            value = row[key]
            if isinstance(value, datetime) or value is None:
                normalized[key] = _canonical_datetime(value) if isinstance(value, datetime) else value
            elif key.endswith("permissions"):
                if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
                    raise ValueError("authorization evidence contains malformed permissions")
                permissions = []
                for permission in value:
                    if not isinstance(permission, str) or not permission:
                        raise ValueError("authorization evidence contains malformed permissions")
                    permissions.append(permission)
                normalized[key] = sorted(set(permissions))
            else:
                normalized[key] = value
        result.append(normalized)
    return result


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AccessScopeDTO",
    "AccessScopeDeniedDTO",
    "AccessScopeFingerprint",
    "AccessScopeResolutionRequestDTO",
    "AccessScopeResolutionResult",
    "AccessScopeResolvedDTO",
    "ActorContextDTO",
    "ActorId",
    "AuthenticationRevision",
    "AuthorizationRevision",
    "ResolvedAccessAuthorizationDTO",
    "RequestedScopeSelectorDTO",
    "ScopeMode",
    "ScopeOperation",
    "SelectorFingerprint",
    "TenantGroupId",
    "TenantId",
    "authentication_revision_for_actor",
    "reauthorize_access_scope",
    "resolve_access_scope",
]
