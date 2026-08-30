"""Canonical RoleGrant reconciliation for one principal.

Two phases with a type-enforced order: :func:`validate_grant_plan` performs
every read and every authorization decision and returns a
:class:`ValidatedGrantPlan`; :func:`sync_membership_grants` accepts only that
token and performs every write. Nothing may be validated after a mutation
(INV-1) — the actor's own ``applicable_grants`` memo is invalidated the moment a
self-grant is written, so a row-by-row "validate then write" loop would let an
actor bootstrap authority mid-transaction.

Attribution requires request context: ``ChangeLoggingMixin`` skips the
``ObjectChange`` when ``get_current_request_id()`` is ``None``. This module does
not set it — non-HTTP callers (management commands, django-q tasks) must wrap
their calls in ``core.tasks.context.TaskContext(tenant_id=..., user_id=...)`` or
their grant changes go unlogged.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.mfa import role_is_privileged
from core.tenant_scope import get_descendant_tenant_group_ids
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from organization.services.errors import (
    ConcurrentGrantChange,
    CrossTenantObject,
    ElevatedGrantIncomplete,
    EscalationDenied,
    MembershipServiceError,
    ServiceError,
)
from organization.services.role_grant_validation import validate_group_membership_grant, validate_role_grant

ManagedScope = Literal["explicit", "tenant_group", "all_managed"]
GrantAction = Literal["created", "updated", "revoked", "unchanged"]

#: Wire value accepted for a managed row covering named tenants. ``explicit`` is
#: the UI's value for "specific tenants"; it maps to ``RoleGrantScope
#: .SCOPE_TENANT`` children. ``organization.services.role_grant_validation.validate_role_grant`` treats
#: "explicit" and "tenant" identically (neither is in its dynamic-scope tuple),
#: so normalising on it is behaviour-preserving.
SCOPE_EXPLICIT: ManagedScope = "explicit"

#: The scope types that make a RoleGrant aggregate a *managed-reach* row.
MANAGED_SCOPE_TYPES = (
    RoleGrantScope.SCOPE_TENANT,
    RoleGrantScope.SCOPE_TENANT_GROUP,
    RoleGrantScope.SCOPE_ALL_MANAGED,
)


@dataclass(frozen=True)
class OwnGrantSpec:
    """One requested own-reach grant.

    INV-5 (own half): ``reason``/``valid_until`` are persisted only when
    ``core.mfa.role_is_privileged(role)``; otherwise they are forced to
    ``""``/``None``. They are applied only to a newly created grant (INV-6).
    """

    role: Role
    reason: str = ""
    valid_until: Optional[datetime] = None


@dataclass(frozen=True)
class ManagedGrantSpec:
    """One requested managed-reach grant aggregate.

    INV-5 (managed half): ``reason``/``valid_until`` are persisted VERBATIM
    regardless of ``role_is_privileged(role)`` — a view-only managed grant may
    legitimately carry an operator-chosen expiry. Do NOT apply
    :class:`OwnGrantSpec`'s privilege gate here: it would silently convert a
    time-boxed managed reach into a permanent one.
    """

    role: Role
    scope: ManagedScope = SCOPE_EXPLICIT
    #: Surviving aggregate; an id that is not a live managed grant of *this*
    #: membership is ignored and the row becomes a new aggregate (INV-7).
    grant_id: Optional[int] = None
    scope_group: Optional[TenantGroup] = None
    tenants: tuple[Tenant, ...] = ()
    reason: str = ""
    valid_until: Optional[datetime] = None
    #: MUST be the index of the row inside ``managed_formset.forms`` (not this
    #: spec's position in ``GrantPlan.managed``): blank, deleted and
    #: already-errored rows are skipped when the intent is built, so the two
    #: indices differ. Non-form callers leave it ``None``. Used only to locate
    #: an error message.
    row_index: Optional[int] = None


@dataclass(frozen=True)
class GrantPlan:
    """What the caller wants this principal's grants to look like.

    There is deliberately NO ``revalidate_inherited_groups`` field. The
    ``is_active`` ``False -> True`` transition that triggers INV-14 is derived
    from stored state by the caller that can observe it, so no submitted payload
    can opt out of the inheritance re-check.
    """

    own: tuple[OwnGrantSpec, ...] = ()
    managed: tuple[ManagedGrantSpec, ...] = ()


@dataclass(frozen=True)
class ValidatedGrantPlan:
    """Produced ONLY by :func:`validate_grant_plan`.

    ``membership_id`` binds the token to the principal it was validated against;
    :func:`sync_membership_grants` requires ``validated.membership_id ==
    membership.pk`` ALWAYS and raises ``ValueError`` otherwise. Without this the
    "only a validated plan may be applied" type gate would still permit applying
    membership A's decision to membership B.

    A create validates before the row exists, so :func:`validate_grant_plan`
    returns ``membership_id=None``. That value is NEVER accepted by the write
    phase: the orchestrator rebinds the token the moment the row is inserted
    (``dataclasses.replace(validated, membership_id=created.pk)``), so an
    unbound token cannot be pointed at a pre-existing membership that happens to
    have no live grants.

    The ``existing_*`` id sets are the state validation reasoned about. They are
    a TAMPER CHECK, not a cache: the apply phase re-reads the live rows under
    the row lock (it needs the ``RoleGrant`` objects and their
    ``RoleGrantScope`` children, which ids cannot supply) and refuses to proceed
    if the sets moved. The apply phase issues no *authorization* query.
    """

    principal_tenant: Tenant
    membership_id: Optional[int]
    plan: GrantPlan
    actor: Optional[object]
    revalidate_inherited_groups: bool
    existing_own_role_ids: frozenset[int]
    existing_managed_grant_ids: frozenset[int]


@dataclass(frozen=True)
class GrantChange:
    """One row the write phase touched (or deliberately left alone)."""

    action: GrantAction
    reach: str  # RoleGrant.REACH_OWN | RoleGrant.REACH_MANAGED
    role_id: int
    grant_id: Optional[int]
    scope_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class GrantSyncResult:
    changes: tuple[GrantChange, ...] = ()

    @property
    def wrote_anything(self) -> bool:
        return any(c.action != "unchanged" for c in self.changes)

    def of(self, action: GrantAction) -> tuple[GrantChange, ...]:
        return tuple(c for c in self.changes if c.action == action)


# ---------------------------------------------------------------------------
# Assignability (INV-3) — model-instance predicates, no widget or queryset input
# ---------------------------------------------------------------------------
def role_assignable_in(role: Role, tenant: Tenant) -> bool:
    """Whether ``role`` may be assigned inside ``tenant``: owned by it, or shared
    down by its managing organization. Mirrors ``RoleGrant.clean``."""
    if role.tenant_id == tenant.pk:
        return True
    return bool(role.shared_with_managed and role.tenant.is_provider and tenant.managed_by_id == role.tenant_id)


def assignable_roles_qs(tenant: Optional[Tenant]) -> QuerySet[Role]:
    """Queryset of roles assignable in ``tenant`` (own + shared-down).

    An unknown tenant (context-free GET) falls back to every live role; the plan
    validation re-checks ownership against the tenant actually submitted, so the
    widened widget queryset carries no authorization weight.
    """
    qs = Role._base_manager.filter(deleted_at__isnull=True).select_related("tenant")
    if tenant is not None:
        ownership = Q(tenant=tenant)
        if tenant.managed_by_id:
            ownership |= Q(
                tenant_id=tenant.managed_by_id,
                tenant__is_provider=True,
                shared_with_managed=True,
            )
        qs = qs.filter(ownership)
    return qs.order_by("name")


def managed_target_tenants_qs(tenant: Optional[Tenant]) -> QuerySet[Tenant]:
    """Tenants a managed-reach row may explicitly target from ``tenant``.

    An unknown tenant falls back to every live tenant for rendering only —
    INV-4 is enforced against the submitted tenant during plan validation.
    """
    qs = Tenant._base_manager.filter(deleted_at__isnull=True)
    if tenant is not None:
        qs = qs.filter(managed_by=tenant)
    return qs.order_by("name")


# ---------------------------------------------------------------------------
# Live-row reads shared by seeding, validation, and the write phase (INV-10)
# ---------------------------------------------------------------------------
def _live() -> Q:
    """Expired grants are inert audit history: never seeded, never revoked."""
    return Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())


def live_own_grants(membership: Membership) -> QuerySet[RoleGrant]:
    """Live own-reach aggregates of ``membership``, scope children prefetched."""
    return (
        RoleGrant.objects.filter(
            membership=membership,
            scopes__scope_type=RoleGrantScope.SCOPE_OWN,
            role__deleted_at__isnull=True,
        )
        .filter(_live())
        .prefetch_related("scopes")
        .distinct()
    )


def live_managed_grants(membership: Membership) -> QuerySet[RoleGrant]:
    """Live managed-reach aggregates of ``membership``, scope children prefetched."""
    return (
        RoleGrant.objects.filter(
            membership=membership,
            role__deleted_at__isnull=True,
            scopes__scope_type__in=MANAGED_SCOPE_TYPES,
        )
        .filter(_live())
        .select_related("role")
        .prefetch_related("scopes", "scopes__tenant", "scopes__tenant_group")
        .distinct()
    )


# ---------------------------------------------------------------------------
# Phase 1 — validation. Read-only, and the ONLY place decisions are taken.
# ---------------------------------------------------------------------------
class _Rejections:
    """Accumulates typed rejections so an admin sees every failure at once.

    Messages are de-duplicated on ``(message, field, row_index)`` — the same
    complaint in the same place is reported once, while the identical message
    on two different rows keeps both locations. First-seen order is preserved.

    The raised class is the single error type when every rejection agrees, and
    the base :class:`MembershipServiceError` when they do not; each
    :class:`ServiceError` keeps its own ``code`` either way, so a caller can
    still tell a cross-tenant object from an escalation without matching text.
    """

    def __init__(self):
        self._entries = []
        self._seen = set()

    def add(self, error_class, message, *, field=None, row_index=None):
        text = str(message)
        key = (text, field, row_index)
        if key in self._seen:
            return
        self._seen.add(key)
        self._entries.append((error_class, ServiceError(text, error_class.default_code, field, row_index)))

    def __bool__(self):
        return bool(self._entries)

    def raise_if_any(self):
        if not self._entries:
            return
        classes = {error_class for error_class, _ in self._entries}
        error_class = classes.pop() if len(classes) == 1 else MembershipServiceError
        raise error_class([error for _, error in self._entries])


def _group_expansion(principal_tenant: Tenant, group: TenantGroup) -> set:
    """Concrete tenants a ``tenant_group`` row covers, exactly as the form
    computed it (deliberately unfiltered by ``deleted_at``, matching the
    coverage the guard has always been handed)."""
    return set(
        Tenant._base_manager.filter(
            managed_by=principal_tenant,
            group_id__in=get_descendant_tenant_group_ids(group.pk),
        ).values_list("pk", flat=True)
    )


def requested_tenant_ids_for(principal_tenant: Tenant, spec: ManagedGrantSpec) -> set[int] | None:
    """The coverage ``organization.services.role_grant_validation.validate_role_grant`` should reason about.

    ``None`` for the dynamic scopes, which the guard resolves from the actor's
    own all-managed authority rather than from a concrete tenant list.
    """
    if spec.scope == RoleGrantScope.SCOPE_ALL_MANAGED:
        return None
    if spec.scope == RoleGrantScope.SCOPE_TENANT_GROUP:
        return _group_expansion(principal_tenant, spec.scope_group)
    return {tenant.pk for tenant in spec.tenants}


def _validate_own_shape(rejections, principal_tenant, plan):
    """Own half of steps 1-3: INV-3 assignability only (own reach has no coverage shape)."""
    for spec in plan.own:
        if not role_assignable_in(spec.role, principal_tenant):
            rejections.add(
                CrossTenantObject,
                _("Role '%(role)s' is not available in the selected tenant.") % {"role": spec.role},
            )


def _validate_managed_row_basics(rejections, principal_tenant, spec, seen_role_ids):
    """Provider/duplicate/assignability/ownership checks for one managed row.

    Returns whether the row is sound enough to also validate its coverage shape.
    """
    row = spec.row_index
    if not principal_tenant.is_provider:
        rejections.add(
            CrossTenantObject,
            _("Only a tenant that manages other tenants can assign roles there."),
            row_index=row,
        )
        return False
    if spec.role.pk in seen_role_ids:
        rejections.add(
            MembershipServiceError,
            _("Role '%(role)s' appears more than once. Combine its managed-tenant coverage in one row.")
            % {"role": spec.role},
            row_index=row,
        )
        return False
    seen_role_ids.add(spec.role.pk)

    if not role_assignable_in(spec.role, principal_tenant):
        rejections.add(
            CrossTenantObject,
            _("Role '%(role)s' is not available in the selected tenant.") % {"role": spec.role},
            row_index=row,
        )
        return False
    # Mirrors RoleGrantScope.clean's managed-shape rule. Without it a role
    # shared DOWN onto a nested-provider membership passes assignability and
    # then raises an uncaught ValidationError inside the write (an HTTP 500).
    if spec.role.tenant_id != principal_tenant.pk or not spec.role.tenant.is_provider:
        rejections.add(
            CrossTenantObject,
            _("Choose a role owned by this managing tenant."),
            row_index=row,
        )
        return False
    return True


def _validate_managed_group_coverage(rejections, principal_tenant, spec):
    row = spec.row_index
    if spec.scope_group is None:
        rejections.add(
            MembershipServiceError,
            _("Choose a tenant group for this coverage."),
            field="scope_group",
            row_index=row,
        )
    elif not _group_expansion(principal_tenant, spec.scope_group):
        rejections.add(
            CrossTenantObject,
            _("The tenant group '%(group)s' covers no tenant managed by %(tenant)s.")
            % {"group": spec.scope_group, "tenant": principal_tenant},
            field="scope_group",
            row_index=row,
        )


def _validate_managed_explicit_coverage(rejections, principal_tenant, spec):
    row = spec.row_index
    if not spec.tenants:
        rejections.add(
            MembershipServiceError,
            _("Pick at least one tenant."),
            field="assigned_tenants",
            row_index=row,
        )
        return
    outside = [tenant for tenant in spec.tenants if tenant.managed_by_id != principal_tenant.pk]
    if outside:
        rejections.add(
            CrossTenantObject,
            _("These tenants are not managed by %(tenant)s: %(names)s")
            % {"tenant": principal_tenant, "names": ", ".join(str(t) for t in outside)},
            field="assigned_tenants",
            row_index=row,
        )


def _validate_managed_row_coverage(rejections, principal_tenant, spec):
    """INV-4 provider reach, dispatched by the row's coverage kind."""
    if spec.scope == RoleGrantScope.SCOPE_ALL_MANAGED:
        return
    if spec.scope == RoleGrantScope.SCOPE_TENANT_GROUP:
        _validate_managed_group_coverage(rejections, principal_tenant, spec)
        return
    _validate_managed_explicit_coverage(rejections, principal_tenant, spec)


def _validate_managed_shape(rejections, principal_tenant, plan):
    """Managed half of steps 1-3, one role per row (A9-A11)."""
    seen_role_ids = set()
    for spec in plan.managed:
        if not _validate_managed_row_basics(rejections, principal_tenant, spec, seen_role_ids):
            continue
        _validate_managed_row_coverage(rejections, principal_tenant, spec)


def _validate_shape_and_reach(rejections, principal_tenant, plan):
    """Steps 1-3: data-integrity rules (A9-A11). Enforced for superusers too."""
    _validate_own_shape(rejections, principal_tenant, plan)
    _validate_managed_shape(rejections, principal_tenant, plan)


def _check_elevated(rejections, reason, valid_until, now, *, row_index):
    if not (reason or "").strip():
        rejections.add(
            ElevatedGrantIncomplete,
            _("Directly assigned elevated roles require a reason."),
            field="reason",
            row_index=row_index,
        )
    if valid_until is None:
        rejections.add(
            ElevatedGrantIncomplete,
            _("Directly assigned elevated roles require an expiration."),
            field="valid_until",
            row_index=row_index,
        )
    elif valid_until <= now:
        rejections.add(
            ElevatedGrantIncomplete,
            _("The expiration must be in the future."),
            field="valid_until",
            row_index=row_index,
        )


def _validate_elevated_metadata(rejections, plan, existing_own_role_ids):
    """Step 4 / INV-5 / A13 — the own and managed halves differ ON PURPOSE.

    Own reach validates only grants this plan would CREATE. Managed reach
    validates every privileged row, new or surviving: ``RoleGrant.clean``
    re-enforces reason/future-expiry on every save of a privileged membership
    grant, so an unvalidated past expiry on a surviving row would reach
    ``grant.save(update_fields=[...])`` and raise inside the write.
    """
    now = timezone.now()
    for spec in plan.own:
        if spec.role.pk in existing_own_role_ids or not role_is_privileged(spec.role):
            continue
        _check_elevated(rejections, spec.reason, spec.valid_until, now, row_index=None)
    for spec in plan.managed:
        if not role_is_privileged(spec.role):
            continue
        _check_elevated(rejections, spec.reason, spec.valid_until, now, row_index=spec.row_index)


def _validate_escalation(rejections, actor, principal_tenant, plan):
    """Step 5 / A4-A8 — ``organization.services.role_grant_validation`` owns the decision; this only routes
    each message to the field or row it belongs on."""
    for spec in plan.own:
        try:
            validate_role_grant(actor, spec.role, principal_tenant, scope_type=RoleGrantScope.SCOPE_OWN)
        except ValidationError as exc:
            for message in exc.messages:
                rejections.add(EscalationDenied, message)
    for spec in plan.managed:
        try:
            validate_role_grant(
                actor,
                spec.role,
                principal_tenant,
                scope_type=spec.scope,
                requested_tenant_ids=requested_tenant_ids_for(principal_tenant, spec),
            )
        except ValidationError as exc:
            for message in exc.messages:
                rejections.add(EscalationDenied, message, row_index=spec.row_index)


def _validate_retained_groups(rejections, actor, membership):
    """Step 6 / INV-14 / A12.

    Switching a principal back on is equivalent to adding it to every retained,
    live group again and must pass the same inheritance guard, including
    provider-managed projections. Inactive/soft-deleted groups stay inert and
    are re-checked if they are themselves reactivated.
    """
    retained = membership.group_memberships.filter(
        user_group__is_active=True,
        user_group__deleted_at__isnull=True,
    ).select_related("user_group")
    for group_membership in retained:
        try:
            validate_group_membership_grant(actor, group_membership.user_group)
        except ValidationError as exc:
            for message in exc.messages:
                rejections.add(EscalationDenied, message)


def validate_grant_plan(
    *,
    actor: object | None,
    principal_tenant: Tenant,
    plan: GrantPlan,
    membership: Optional[Membership] = None,
    revalidate_inherited_groups: bool = False,
) -> ValidatedGrantPlan:
    """Read-only. Raises :class:`MembershipServiceError` (aggregated) on rejection.

    ``revalidate_inherited_groups`` is DERIVED by the caller from the
    membership's stored ``is_active`` (INV-14) — by ``execute_membership_write``
    from the ``select_for_update()``-locked row (the authoritative derivation),
    by ``MembershipForm.clean`` from the row as loaded (error reporting only),
    and by ``apply_membership_grants`` from ``previous_is_active`` with ``None``
    failing closed. It is never read from submitted data.

    Order of checks, all read-only:

      1. shape — a managed row on a non-provider tenant; a duplicate role across
         managed rows; an explicit row with no tenants; a group row with no group.
      2. INV-3 assignability for every own role and every managed row's role.
      3. INV-4 provider reach — each explicit tenant is ``managed_by`` the
         principal tenant, a group row expands through
         ``organization.access.get_descendant_tenant_group_ids``, and the
         managed shape mirrors ``RoleGrantScope.clean``.
      4. INV-5 elevated metadata, own/managed asymmetry preserved exactly.
      5. ``organization.services.role_grant_validation.validate_role_grant`` per own role and managed row.
      6. INV-14 ``validate_group_membership_grant`` per retained live group.

    Steps 1-3 are data-integrity rules and halt before the actor-relative
    steps: running an escalation guard against a role that is not even
    assignable in this tenant would only add a confusing second message.

    Error location is part of the contract. Own-reach escalation (A4) and
    reactivation (A12) rejections carry ``field=None, row_index=None`` so they
    stay in ``form.non_field_errors()``; only managed-row rejections carry
    ``row_index``; only shape/required-ness rejections carry ``field``.
    """
    existing_own_role_ids = frozenset()
    existing_managed_grant_ids = frozenset()
    if membership is not None and membership.pk:
        existing_own_role_ids = frozenset(live_own_grants(membership).values_list("role_id", flat=True))
        existing_managed_grant_ids = frozenset(live_managed_grants(membership).values_list("pk", flat=True))

    rejections = _Rejections()
    _validate_shape_and_reach(rejections, principal_tenant, plan)
    rejections.raise_if_any()

    _validate_elevated_metadata(rejections, plan, existing_own_role_ids)
    _validate_escalation(rejections, actor, principal_tenant, plan)
    if revalidate_inherited_groups and membership is not None and membership.pk:
        _validate_retained_groups(rejections, actor, membership)
    rejections.raise_if_any()

    return ValidatedGrantPlan(
        principal_tenant=principal_tenant,
        membership_id=membership.pk if membership is not None else None,
        plan=plan,
        actor=actor,
        revalidate_inherited_groups=revalidate_inherited_groups,
        existing_own_role_ids=existing_own_role_ids,
        existing_managed_grant_ids=existing_managed_grant_ids,
    )


# ---------------------------------------------------------------------------
# Phase 2 — the write. Never re-reads authorization.
# ---------------------------------------------------------------------------
def _desired_scope_keys(spec: ManagedGrantSpec):
    """The ``(scope_type, tenant_id, tenant_group_id)`` children a row wants."""
    if spec.scope == RoleGrantScope.SCOPE_ALL_MANAGED:
        return {(RoleGrantScope.SCOPE_ALL_MANAGED, None, None)}
    if spec.scope == RoleGrantScope.SCOPE_TENANT_GROUP:
        return {(RoleGrantScope.SCOPE_TENANT_GROUP, None, spec.scope_group.pk)}
    return {(RoleGrantScope.SCOPE_TENANT, tenant.pk, None) for tenant in spec.tenants}


def _sync_own_reach(membership, validated, changes):
    """Pass 1 of §7.3 — own reach, reconciled against ``plan.own``."""
    selected = list(validated.plan.own)
    selected_ids = {spec.role.pk for spec in selected}
    existing_by_role = {}
    for existing in live_own_grants(membership):
        existing_by_role.setdefault(existing.role_id, existing)
        if existing.role_id in selected_ids:
            continue
        # Per-object deletes so every revocation writes an ObjectChange (INV-8),
        # and only the OWN child: a managed scope on the same aggregate is the
        # other reach's business (INV-9).
        for scope in list(existing.scopes.all()):
            if scope.scope_type == RoleGrantScope.SCOPE_OWN:
                scope.delete()
        revoked_id = existing.pk
        if not RoleGrantScope.objects.filter(role_grant=existing).exists():
            existing.delete()
        changes.append(
            GrantChange(
                action="revoked",
                reach=RoleGrant.REACH_OWN,
                role_id=existing.role_id,
                grant_id=revoked_id,
                scope_types=(RoleGrantScope.SCOPE_OWN,),
            )
        )

    for spec in selected:
        surviving = existing_by_role.get(spec.role.pk)
        if surviving is not None:
            changes.append(
                GrantChange(
                    action="unchanged",
                    reach=RoleGrant.REACH_OWN,
                    role_id=spec.role.pk,
                    grant_id=surviving.pk,
                    scope_types=(RoleGrantScope.SCOPE_OWN,),
                )
            )
            continue
        # INV-5 own half: metadata is gated on privilege AT WRITE TIME, so a
        # non-privileged role never silently carries a submitted expiry.
        privileged = role_is_privileged(spec.role)
        created = RoleGrant(
            membership=membership,
            role=spec.role,
            granted_by=validated.actor,
            reason=spec.reason if privileged else "",
            valid_until=spec.valid_until if privileged else None,
        )
        created.save()
        RoleGrantScope.objects.create(role_grant=created, scope_type=RoleGrantScope.SCOPE_OWN)
        changes.append(
            GrantChange(
                action="created",
                reach=RoleGrant.REACH_OWN,
                role_id=spec.role.pk,
                grant_id=created.pk,
                scope_types=(RoleGrantScope.SCOPE_OWN,),
            )
        )


def _intended_managed_rows(validated, existing):
    """Pass 2 of §7.3 — pair each intended row with the aggregate it claims.

    An id that is not a live managed grant of THIS membership is dropped, so a
    stray or tampered id becomes a new row and can never touch another
    membership's grant (INV-7). A role change forces a new aggregate: the old
    row must die as a change-logged revocation because ``granted_by`` /
    ``granted_at`` document who granted THAT role (INV-6).
    """
    kept = []
    for spec in validated.plan.managed:
        claimed = existing.get(spec.grant_id) if spec.grant_id in existing else None
        if claimed is not None and claimed.role_id != spec.role.pk:
            claimed = None
        kept.append((claimed, spec))
    return kept


def _revoke_obsolete_managed_grants(existing, retained_ids, changes):
    """Pass 3 of §7.3 — revoke every managed aggregate the plan omitted, preserving
    a possible own scope on the same aggregate (INV-9)."""
    for pk, obsolete in existing.items():
        if pk in retained_ids:
            continue
        removed = []
        for child in list(obsolete.scopes.all()):
            if child.scope_type != RoleGrantScope.SCOPE_OWN:
                removed.append(child.scope_type)
                child.delete()
        if not RoleGrantScope.objects.filter(role_grant=obsolete).exists():
            obsolete.delete()
        changes.append(
            GrantChange(
                action="revoked",
                reach=RoleGrant.REACH_MANAGED,
                role_id=obsolete.role_id,
                grant_id=pk,
                scope_types=tuple(sorted(removed)),
            )
        )


def _upsert_managed_grant(membership, validated, claimed, spec):
    """Create a new managed-reach aggregate, or update a surviving one's
    reason/expiry in place. Returns ``(grant, touched)`` where ``touched`` is
    ``"created"``, ``"updated"``, or ``False``."""
    if claimed is None:
        # INV-5 managed half: metadata is stored VERBATIM — a view-only
        # managed grant may legitimately carry an operator-chosen expiry.
        claimed = RoleGrant(
            membership=membership,
            role=spec.role,
            granted_by=validated.actor,
            reason=spec.reason,
            valid_until=spec.valid_until,
        )
        claimed.save()
        return claimed, "created"

    changed_fields = False
    if claimed.reason != spec.reason:
        claimed.reason = spec.reason
        changed_fields = True
    if claimed.valid_until != spec.valid_until:
        claimed.valid_until = spec.valid_until
        changed_fields = True
    if changed_fields:
        claimed.save(update_fields=["reason", "valid_until"])
        return claimed, "updated"
    return claimed, False


def _sync_managed_row(membership, validated, claimed, spec, changes):
    """Pass 4 of §7.3 — create or update one aggregate, then diff its scope children."""
    claimed, touched = _upsert_managed_grant(membership, validated, claimed, spec)

    desired = _desired_scope_keys(spec)
    current = {
        (child.scope_type, child.tenant_id, child.tenant_group_id): child
        for child in claimed.scopes.all()
        if child.scope_type != RoleGrantScope.SCOPE_OWN
    }
    for key, child in current.items():
        if key not in desired:
            child.delete()
            touched = touched or "updated"
    for scope_type, tenant_id, tenant_group_id in desired - set(current):
        RoleGrantScope.objects.create(
            role_grant=claimed,
            scope_type=scope_type,
            tenant_id=tenant_id,
            tenant_group_id=tenant_group_id,
        )
        touched = touched or "updated"

    changes.append(
        GrantChange(
            action=touched or "unchanged",
            reach=RoleGrant.REACH_MANAGED,
            role_id=spec.role.pk,
            grant_id=claimed.pk,
            scope_types=tuple(sorted(scope_type for scope_type, _t, _g in desired)),
        )
    )


def _sync_managed_reach(membership, validated, changes):
    """Passes 2-4 of §7.3 — managed reach, reconciled against ``plan.managed``.

    The existing rows are re-read here rather than reused from the own pass:
    that pass may have deleted an OWN child from an aggregate this one also
    touches, and a stale prefetch cache would then diff against scope rows that
    no longer exist.
    """
    existing = {existing_grant.pk: existing_grant for existing_grant in live_managed_grants(membership)}
    kept = _intended_managed_rows(validated, existing)
    retained_ids = {claimed.pk for claimed, _spec in kept if claimed is not None}

    _revoke_obsolete_managed_grants(existing, retained_ids, changes)
    for claimed, spec in kept:
        _sync_managed_row(membership, validated, claimed, spec, changes)


def sync_membership_grants(*, membership: Membership, validated: ValidatedGrantPlan) -> GrantSyncResult:
    """Write phase. Applies ``validated`` in the order fixed by §7.3 and never
    re-reads authorization.

    Preconditions, each enforced rather than documented:

      * ``validated`` is a :class:`ValidatedGrantPlan`; a raw :class:`GrantPlan`
        raises ``TypeError`` — INV-1 as a type.
      * the caller is inside a transaction, otherwise ``RuntimeError``. Not an
        ``assert``: assertions are stripped under ``-O`` and this is a
        write-safety gate, not a debugging aid.
      * ``validated.membership_id == membership.pk``, otherwise ``ValueError``.
        ``None`` is never accepted — the orchestrator rebinds the token
        immediately after the insert on a create.
      * ``validated.principal_tenant`` is the membership's own tenant, otherwise
        ``ValueError``. Assignability (INV-3), provider reach (INV-4) and the
        escalation guard are all decided RELATIVE to that tenant, so applying
        the token to a membership living somewhere else would mean every one of
        those decisions was taken in the wrong tenant. Defence in depth behind
        ``membership.plan_membership_write``'s own binding.
      * the live existing-row id sets re-read under the lock still equal the
        ones validation reasoned about, otherwise :class:`ConcurrentGrantChange`.

    The membership row lock is RE-ACQUIRED here rather than asserted: re-locking
    inside the owning transaction is a no-op, and no portable check for "the
    caller already locked this row" exists — Postgres exposes no per-row
    lock-ownership test to the ORM and a second ``select_for_update()`` always
    succeeds for the transaction that holds it. Re-acquiring makes the
    precondition true instead of claiming to verify it.
    """
    if not isinstance(validated, ValidatedGrantPlan):
        raise TypeError(
            "sync_membership_grants accepts only a ValidatedGrantPlan produced by "
            f"validate_grant_plan, not {type(validated).__name__}."
        )
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "sync_membership_grants must run inside a transaction so a partial reconciliation can never be committed."
        )
    if membership.pk is None or validated.membership_id != membership.pk:
        raise ValueError(
            f"ValidatedGrantPlan is bound to membership {validated.membership_id!r}, not {membership.pk!r}."
        )
    if validated.principal_tenant.pk != membership.tenant_id:
        raise ValueError(
            f"ValidatedGrantPlan is bound to tenant {validated.principal_tenant.pk!r}, not {membership.tenant_id!r}."
        )

    locked = Membership.objects.select_for_update().get(pk=membership.pk)
    live_own = frozenset(live_own_grants(locked).values_list("role_id", flat=True))
    live_managed = frozenset(live_managed_grants(locked).values_list("pk", flat=True))
    if live_own != validated.existing_own_role_ids or live_managed != validated.existing_managed_grant_ids:
        # An unlocked legacy writer (§7.5) can legitimately win this race, so
        # this is a typed, non-field service error the form can re-render —
        # not a 500. ValueError stays reserved for programmer errors.
        raise ConcurrentGrantChange.single(
            str(_("Another change to this membership's roles landed first. Review and resubmit."))
        )

    changes = []
    _sync_own_reach(locked, validated, changes)
    _sync_managed_reach(locked, validated, changes)
    return GrantSyncResult(changes=tuple(changes))
