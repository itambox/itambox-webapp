"""Membership lifecycle: actor authorization, identity resolution, the
``Membership`` row, and grant orchestration.

This is the single write entry point for interactive membership + RoleGrant
changes. Every authorization decision is taken here or in
``organization.services.rolegrants``, from model instances and an actor — never
from a widget queryset or ``cleaned_data``, so a directly-constructed form, a
tampered POST, or a future API caller is gated identically.

Attribution requires request context: ``ChangeLoggingMixin`` skips the
``ObjectChange`` when ``get_current_request_id()`` is ``None``. Setting it stays
the caller's job — non-HTTP callers (management commands, django-q tasks) must
wrap their calls in ``core.tasks.context.TaskContext(tenant_id=..., user_id=...)``
or their membership and grant changes go unlogged.
"""

import dataclasses
from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from organization.models import Membership, Tenant
from organization.services.errors import (
    ActorNotAuthorized,
    AmbiguousIdentity,
    DuplicateMembership,
)
from organization.services.rolegrants import (
    GrantPlan,
    GrantSyncResult,
    ManagedGrantSpec,
    OwnGrantSpec,
    ValidatedGrantPlan,
    sync_membership_grants,
    validate_grant_plan,
)
from users.services import AmbiguousEmailError, resolve_existing_user, resolve_or_create_user


@dataclass(frozen=True)
class NewIdentitySpec:
    """The who=new block. ``email`` is normalised by the service."""

    email: str
    first_name: str = ""
    last_name: str = ""


@dataclass(frozen=True)
class MembershipIntent:
    """What the caller wants a membership (and its grants) to look like.

    ``user`` takes precedence over ``new_identity`` when both are supplied: an
    explicitly chosen account is never overridden by an email lookup.
    """

    tenant: Tenant
    is_active: bool = True
    user: Optional[object] = None
    new_identity: Optional[NewIdentitySpec] = None
    own_roles: tuple[OwnGrantSpec, ...] = ()
    managed_grants: tuple[ManagedGrantSpec, ...] = ()

    def grant_plan(self) -> GrantPlan:
        return GrantPlan(own=tuple(self.own_roles), managed=tuple(self.managed_grants))


@dataclass(frozen=True)
class MembershipWritePlan:
    """Read-only outcome of planning; carries the ``ValidatedGrantPlan`` token."""

    intent: MembershipIntent
    actor: Optional[object]
    membership: Optional[Membership]  # None on create
    resolved_user: Optional[object]  # None => identity must be created on apply
    will_create_identity: bool
    validated_grants: ValidatedGrantPlan


@dataclass(frozen=True)
class MembershipWriteResult:
    membership: Membership
    membership_created: bool
    identity_created: bool
    grants: GrantSyncResult


# ---------------------------------------------------------------------------
# Authorization (matrix A1-A3)
# ---------------------------------------------------------------------------
def may_manage_memberships(*, actor, tenant: Tenant, creating: bool) -> bool:
    """Whether ``actor`` may add/change memberships in ``tenant``.

    An absent actor (system/programmatic contexts — seeds, management commands,
    SSO provisioning) and a superuser are trusted (INV-2); otherwise the
    relevant object-level Django permission is required. This is the boolean
    form the form needs for its membership-oracle defence, without catching an
    exception; role-permission escalation is a separate, unconditional check.
    """
    if actor is None or getattr(actor, "is_superuser", False):
        return True
    perm = "organization.add_membership" if creating else "organization.change_membership"
    return bool(actor.has_perm(perm, obj=tenant))


def authorize_membership_write(*, actor, tenant: Tenant, creating: bool) -> None:
    """Object-level gate at the SERVICE boundary — not only in the form/view.

    Raises :class:`ActorNotAuthorized`. The message deliberately does not name
    the tenant: on this path the tenant came from submitted data, so echoing its
    name back would confirm the existence of a tenant the actor may not see.
    """
    if may_manage_memberships(actor=actor, tenant=tenant, creating=creating):
        return
    raise ActorNotAuthorized.single(str(_("You are not allowed to manage memberships in the selected tenant.")))


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def resolve_identity(*, spec: NewIdentitySpec):
    """Resolve-only (never create) via ``users.services.resolve_existing_user``.

    Raises :class:`AmbiguousIdentity` when more than one account matches: email
    is deliberately not globally unique in this model, so ambiguity fails closed
    rather than silently picking the lowest-PK row.
    """
    try:
        return resolve_existing_user(spec.email)
    except AmbiguousEmailError:
        raise AmbiguousIdentity.single(
            str(
                _(
                    "More than one account already uses this email address — "
                    "resolve the duplicate before adding a membership."
                )
            ),
            field="new_user_email",
        ) from None


def _membership_exists(user, tenant: Tenant) -> bool:
    """Whether ``user`` already belongs to ``tenant``.

    A single seam so the create-path pre-check and the post-``IntegrityError``
    discriminator agree, and so the race between them can be driven
    deterministically in tests.
    """
    return Membership.objects.filter(user=user, tenant=tenant).exists()


# ---------------------------------------------------------------------------
# Planning (read-only) and the single write entry point
# ---------------------------------------------------------------------------
def plan_membership_write(
    *,
    actor,
    intent: MembershipIntent,
    membership: Optional[Membership] = None,
    revalidate_inherited_groups: bool = False,
) -> MembershipWritePlan:
    """Read-only. Safe to call from a form's ``clean()`` — it writes nothing.

    Runs, in order:

      1. :func:`authorize_membership_write` — **raises immediately on failure.
         Its error is NEVER aggregated with steps 2-3.** Those steps can
         disclose target state (``DuplicateMembership`` names the tenant the
         account already belongs to; ``AmbiguousIdentity`` confirms a duplicate
         email), so an unauthorized actor must never reach them. This
         short-circuit is what keeps INV-12 true at the service boundary.
      2. identity resolution, and duplicate-membership detection **only on a
         create**: on an update the ``(user, tenant)`` pair is the row being
         edited.
      3. ``rolegrants.validate_grant_plan`` (which aggregates internally).

    On an UPDATE the principal tenant is the membership's own tenant, never the
    submitted ``intent.tenant``: the tenant whose members are being changed is
    the one A1/A2 has to be evaluated against, and the one the grant plan's
    assignability and escalation decisions must reason about. Authorizing
    against the submitted tenant instead would let an actor who manages a
    provider prove authority *there* and have the grant written into a customer
    they hold nothing in. A mismatch is then refused outright, mirroring the
    ``intent.user`` guard below — a membership's tenant is immutable, so no
    caller can legitimately ask for one.

    ``revalidate_inherited_groups`` is forwarded to ``validate_grant_plan``.
    Every caller that can observe a stored ``is_active`` MUST derive and pass it
    (INV-14). The default is ``False`` only for callers planning a CREATE, where
    no prior ``is_active`` exists.
    """
    creating = membership is None
    principal_tenant = intent.tenant if creating else membership.tenant
    authorize_membership_write(actor=actor, tenant=principal_tenant, creating=creating)
    if not creating and intent.tenant.pk != membership.tenant_id:
        # Raised only after the gate: the check names the membership's tenant, so
        # reporting it to an unauthorized actor would be a disclosure (INV-12).
        raise ValueError(
            "MembershipIntent.tenant does not match the membership being updated; a membership's tenant is immutable."
        )

    resolved_user = intent.user
    will_create_identity = False
    if not creating:
        if resolved_user is not None and resolved_user.pk != membership.user_id:
            # The user is immutable on an edit; silently ignoring a mismatch
            # would write a row the caller did not ask for.
            raise ValueError(
                "MembershipIntent.user does not match the membership being updated; a membership's user is immutable."
            )
        resolved_user = membership.user
    elif resolved_user is None and intent.new_identity is not None:
        # Documented precedence: an explicitly chosen account always wins over
        # the who=new email block.
        resolved_user = resolve_identity(spec=intent.new_identity)
        will_create_identity = resolved_user is None

    if creating and resolved_user is not None and _membership_exists(resolved_user, intent.tenant):
        raise DuplicateMembership.single(
            str(
                _("%(user)s is already a member of %(tenant)s — edit their membership instead.")
                % {"user": resolved_user, "tenant": intent.tenant}
            ),
            field="new_user_email" if intent.user is None else None,
        )

    validated = validate_grant_plan(
        actor=actor,
        principal_tenant=principal_tenant,
        plan=intent.grant_plan(),
        membership=membership,
        revalidate_inherited_groups=revalidate_inherited_groups,
    )
    return MembershipWritePlan(
        intent=intent,
        actor=actor,
        membership=membership,
        resolved_user=resolved_user,
        will_create_identity=will_create_identity,
        validated_grants=validated,
    )


def _insert_membership(*, user, intent: MembershipIntent) -> Membership:
    """Insert the row, isolated in its own savepoint.

    The ``(user, tenant)`` unique constraint is the concurrency backstop for a
    create (INV-12's read can legitimately have seen nothing). Isolating the
    insert keeps the surrounding transaction usable, so the collision surfaces
    as a typed :class:`DuplicateMembership` rather than an aborted transaction.
    An ``IntegrityError`` that is NOT that collision is re-raised untouched —
    masking it as "already a member" would be a lie.
    """
    row = Membership(user=user, tenant=intent.tenant, is_active=intent.is_active)
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError:
        if not _membership_exists(user, intent.tenant):
            raise
        raise DuplicateMembership.single(
            str(
                _("%(user)s is already a member of %(tenant)s — edit their membership instead.")
                % {"user": user, "tenant": intent.tenant}
            )
        ) from None
    return row


def _apply(plan: MembershipWritePlan) -> MembershipWriteResult:
    """Identity -> membership row -> grants (§7.1). Caller owns the transaction."""
    intent = plan.intent
    user = plan.resolved_user
    identity_created = False
    if plan.will_create_identity:
        # INV-11: the insert is delegated so it keeps its savepoint and its
        # IntegrityError re-resolve; a direct ``User(...)`` here would lose both.
        user, identity_created = resolve_or_create_user(
            email=intent.new_identity.email,
            first_name=intent.new_identity.first_name,
            last_name=intent.new_identity.last_name,
        )

    validated = plan.validated_grants
    membership = plan.membership
    membership_created = False
    if membership is None:
        membership = _insert_membership(user=user, intent=intent)
        membership_created = True
        # The token was minted before the row existed; bind it now so an unbound
        # token can never be pointed at a pre-existing membership.
        validated = dataclasses.replace(validated, membership_id=membership.pk)
    elif membership.is_active != intent.is_active:
        membership.is_active = intent.is_active
        membership.save(update_fields=["is_active"])

    grants = sync_membership_grants(membership=membership, validated=validated)
    return MembershipWriteResult(
        membership=membership,
        membership_created=membership_created,
        identity_created=identity_created,
        grants=grants,
    )


def execute_membership_write(
    *,
    actor,
    intent: MembershipIntent,
    membership: Optional[Membership] = None,
) -> MembershipWriteResult:
    """The single write entry point (§7).

    Opens ``transaction.atomic()``, takes a ``select_for_update()`` row lock on
    the membership (when one exists), DERIVES the INV-14 reactivation transition
    from the locked row, RE-PLANS inside the lock, then applies. Raises the same
    typed errors :func:`plan_membership_write` does.

    The lock SERIALISES concurrent writers of this membership; under PostgreSQL
    READ COMMITTED it does not confer snapshot isolation, so unrelated tables
    (``Role``, ``RoleGrant``, ``TenantGroup``) may still move between
    statements — which is exactly what ``sync_membership_grants``'s
    tamper check catches.
    """
    with transaction.atomic():
        locked = None
        if membership is not None:
            locked = Membership.objects.select_for_update().get(pk=membership.pk)
        plan = plan_membership_write(
            actor=actor,
            intent=intent,
            membership=locked,
            # INV-14, derived from the LOCKED row — the authoritative derivation.
            revalidate_inherited_groups=bool(locked is not None and locked.is_active is False and intent.is_active),
        )
        return _apply(plan)


def apply_membership_grants(
    *,
    actor,
    membership: Membership,
    plan: GrantPlan,
    previous_is_active: Optional[bool] = None,
) -> GrantSyncResult:
    """Grant-only entry point for callers that already own the Membership row —
    used by ``MembershipForm.save(commit=False)``'s deferred ``save_m2m``.

    It is a FULL write path, not a shortcut: it opens ``transaction.atomic()``,
    takes the same ``select_for_update()`` lock, authorizes the actor, validates
    the plan, and only then writes. Anything less would make ``save(commit=False)``
    + ``save_m2m()`` a hole in matrix rows A1/A2 and in §7.5's serialisation.

    ``previous_is_active`` is the membership's stored ``is_active`` BEFORE the
    caller wrote the row. The caller has already persisted it by the time this
    runs, so the locked row can no longer show a ``False -> True`` transition
    and this service cannot derive it (INV-14). The derivation is therefore
    ``(previous_is_active is not True) and membership.is_active`` — ``None``
    (unknown) FAILS CLOSED and re-validates every retained live group. Skipping
    the guard requires the caller to positively assert
    ``previous_is_active=True``; omission never disables it.

    Authorization note: this entry point gates on ``change_membership`` (A2)
    because it cannot know whether the caller's write created the row. An actor
    holding only ``add_membership`` can therefore complete a create via
    ``save(commit=True)`` but not via ``save(commit=False)`` + ``save_m2m()``.
    """
    with transaction.atomic():
        locked = Membership.objects.select_for_update().get(pk=membership.pk)
        authorize_membership_write(actor=actor, tenant=locked.tenant, creating=False)
        validated = validate_grant_plan(
            actor=actor,
            principal_tenant=locked.tenant,
            plan=plan,
            membership=locked,
            revalidate_inherited_groups=bool((previous_is_active is not True) and locked.is_active),
        )
        return sync_membership_grants(membership=locked, validated=validated)
