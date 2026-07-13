"""Domain/service-layer helpers for the organization app.

Holds the tenant-visibility helper shared by the ``Membership`` UI views and
any model-agnostic code (e.g. ``ObjectExportView``) that needs the same
restriction applied to a model whose default manager does not filter by
tenant, plus the centralized cross-tenant resource-access resolver
(ADR-0001, remediation plan phase 3).
"""
from dataclasses import dataclass
from typing import Optional

from core.managers import get_current_tenant

from .access import accessible_tenant_ids, get_ancestor_tenant_group_ids
from .models import Tenant, TenantResourceGrant


# Access-control models whose default manager is deliberately unscoped (their
# tenant resolution is itself an *input* to tenant scoping, so they cannot ride
# the tenant-scoping manager — see TenantScopingQuerySet.filter_by_tenant() in
# core/managers.py). Generic, model-agnostic code (ObjectExportView) must apply
# ``visible_to_containers`` to these instead.
_UNFILTERED_CONTAINER_MODELS = {
    ('organization', 'membership'),
    ('organization', 'roleassignment'),
    ('organization', 'tenantresourcegrant'),
    ('users', 'token'),
}


def visible_to_containers(user, qs, perm):
    """Restrict a queryset of tenant-anchored rows (``Membership``,
    ``RoleAssignment``, ``users.Token``) to the tenants ``user`` actually
    holds ``perm`` in.

    These models intentionally use an unscoped default manager, so
    ``TenantScopingViewMixin.get_queryset()`` — and any other generic,
    model-agnostic view built on ``filter_by_tenant()``, such as
    ``ObjectExportView`` — is a silent no-op for them; callers must restrict
    the queryset manually with this helper. Candidate tenants come from
    ``accessible_tenant_ids`` (membership + group + managed reach), then each
    is checked with ``user.has_perm(perm, obj=tenant)``. Superusers are
    unaffected (``PermissionsMixin.has_perm`` short-circuits ``True`` before
    any backend runs); multi-tenant staff still see every tenant they hold
    ``perm`` in, including via managed reach.
    """
    if user.is_superuser:
        return qs
    candidate_ids = accessible_tenant_ids(user)
    allowed = [
        t.pk for t in Tenant._base_manager.filter(
            pk__in=candidate_ids, deleted_at__isnull=True,
        )
        if user.has_perm(perm, obj=t)
    ]
    model = qs.model
    field_names = {f.name for f in model._meta.get_fields()}
    if 'tenant' in field_names:
        return qs.filter(tenant_id__in=allowed)
    if 'membership' in field_names:
        # RoleAssignment-shaped rows anchor to a tenant via their membership.
        return qs.filter(membership__tenant_id__in=allowed)
    return qs.none()  # unknown shape — fail closed


def is_container_scoped_unfiltered(model):
    """True for the access-control models (``Membership``, ``RoleAssignment``,
    ``users.Token``) that carry a tenant anchor but whose default manager does
    not filter by tenant (see ``visible_to_containers``). Model-agnostic code
    that would otherwise rely on ``filter_by_tenant`` (e.g. ``ObjectExportView``)
    uses this to detect when it must apply ``visible_to_containers`` instead.
    """
    return (model._meta.app_label, model._meta.model_name) in _UNFILTERED_CONTAINER_MODELS


# ---------------------------------------------------------------------------
# Cross-tenant resource-access resolver (ADR-0001, remediation plan phase 3)
# ---------------------------------------------------------------------------

#: view < use — a 'use' grant satisfies a 'view' request, never vice versa.
_ACCESS_ORDER = {
    TenantResourceGrant.ACCESS_VIEW: 0,
    TenantResourceGrant.ACCESS_USE: 1,
}

# Machine-readable decision reasons.
REASON_SAME_TENANT = 'same-tenant'
REASON_DIRECT_GRANT = 'direct-grant'
REASON_GROUP_GRANT = 'group-grant'
DENIED_NO_ACTIVE_TENANT = 'no-active-tenant'
DENIED_OWNER_UNRESOLVABLE = 'owner-unresolvable'
DENIED_NO_GRANT = 'no-grant'
DENIED_INSUFFICIENT_LEVEL = 'insufficient-access-level'
DENIED_RBAC = 'rbac-denied'


@dataclass(frozen=True)
class ResourceAccessDecision:
    """The resolver's verdict. ``grant`` is the exact row that authorized a
    cross-tenant access so the caller can record provenance (phase 4:
    ``assignment.resource_grant``); it is ``None`` for same-tenant access."""
    allowed: bool
    reason: str
    owner_tenant_id: Optional[int] = None
    grant: Optional[TenantResourceGrant] = None


def resolve_stock_access(user, stock, access_level, perm, active_tenant=None):
    """THE authorization path for stock-pool access — used by UI, REST,
    GraphQL, imports, and background tasks alike (ADR-0001 phase 3).

    Flow:
      1. Resolve the pool's owner from ``stock.location.tenant``.
      2. If ``active_tenant`` owns it, apply normal RBAC only.
      3. Otherwise find an explicit direct grant, or a grant targeting an
         ancestor TenantGroup of the active tenant's group.
      4. Verify the requested ``access_level`` against the grant's.
      5. Independently verify ``perm`` in the user's active tenant — a grant
         authorizes the TENANT, never the user.
      6. Return the exact grant used so assignments can record provenance.

    Deliberately non-transitive and non-recursive: the only comparison is
    owner vs. active tenant. A grant held by a third tenant never chains, and
    receiving stock never makes the recipient an owner. Cross-tenant access
    without a live grant is denied for superusers too — the grant is a
    data-model invariant (phase-4 provenance), not a user privilege; the RBAC
    step is where superusers pass unconditionally.
    """
    if active_tenant is None:
        active_tenant = get_current_tenant()
    if active_tenant is None:
        return ResourceAccessDecision(False, DENIED_NO_ACTIVE_TENANT)

    owner_tenant_id = stock.location.tenant_id
    if owner_tenant_id is None:
        # A pool at a tenant-less location has no provable owner — fail
        # closed (phase-1 integrity report surfaces these rows).
        return ResourceAccessDecision(False, DENIED_OWNER_UNRESOLVABLE)

    def rbac_ok():
        # System contexts (imports, seeds, background tasks outside
        # TaskContext) carry no user: the tenant-level grant is the gate
        # there — the RBAC step applies to real actors.
        return user is None or user.has_perm(perm, obj=active_tenant)

    if owner_tenant_id == active_tenant.pk:
        if not rbac_ok():
            return ResourceAccessDecision(False, DENIED_RBAC, owner_tenant_id)
        return ResourceAccessDecision(True, REASON_SAME_TENANT, owner_tenant_id)

    grant, reason = _find_covering_grant(owner_tenant_id, active_tenant, stock)
    if grant is None:
        return ResourceAccessDecision(False, DENIED_NO_GRANT, owner_tenant_id)

    if _ACCESS_ORDER[grant.access_level] < _ACCESS_ORDER[access_level]:
        return ResourceAccessDecision(
            False, DENIED_INSUFFICIENT_LEVEL, owner_tenant_id, grant,
        )
    if not rbac_ok():
        return ResourceAccessDecision(False, DENIED_RBAC, owner_tenant_id, grant)
    return ResourceAccessDecision(True, reason, owner_tenant_id, grant)


def _find_covering_grant(owner_tenant_id, active_tenant, stock):
    """The live grant covering (pool, active tenant), preferring a direct
    grant over an ancestor-group grant. Returns ``(grant, reason)``."""
    from django.contrib.contenttypes.models import ContentType
    ct = ContentType.objects.get_for_model(type(stock))
    base = TenantResourceGrant.objects.filter(
        tenant_id=owner_tenant_id,
        resource_type=ct,
        resource_id=stock.pk,
    )
    grant = base.filter(grantee_tenant=active_tenant).first()
    if grant is not None:
        return grant, REASON_DIRECT_GRANT
    ancestor_group_ids = get_ancestor_tenant_group_ids(active_tenant.group_id)
    if ancestor_group_ids:
        grant = (
            base.filter(grantee_tenant_group_id__in=ancestor_group_ids)
            .order_by('created_at').first()
        )
        if grant is not None:
            return grant, REASON_GROUP_GRANT
    return None, DENIED_NO_GRANT
