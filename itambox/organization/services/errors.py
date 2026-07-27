"""Typed, field-mappable rejections for the membership/RBAC services.

Every error is a :class:`django.core.exceptions.ValidationError` (the same class
``django.forms`` re-exports), so existing ``except ValidationError`` /
``exc.messages`` callers are unaffected. The extra ``errors`` tuple lets a form
put each message back on the field or formset row it came from without parsing
strings.

**Message-disclosure rule.** :class:`ActorNotAuthorized` is the only error that
may be raised before the actor has been authorized for the target tenant; every
other error here can disclose target state (whether an account exists, whether
it already belongs to the tenant, which tenants a provider manages) and is
therefore unreachable until ``authorize_membership_write`` has passed. See
``membership.plan_membership_write``'s mandatory short-circuit and INV-12.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class ServiceError:
    """One rejection, located precisely enough for a form to render it.

    ``field`` is a form/row field name (``None`` => a non-field error) and
    ``row_index`` is an index into ``managed_formset.forms`` (``None`` => the
    main form). Frozen so a caller cannot re-point a message after the fact.
    """

    message: str
    code: str
    field: Optional[str] = None
    row_index: Optional[int] = None


class MembershipServiceError(ValidationError):
    """Base for every membership/RBAC service rejection.

    Subclasses exist so callers can distinguish *why* a write was refused
    without matching on message text; the aggregated ``messages`` list stays
    exactly what a ``ValidationError`` consumer already expects.
    """

    default_code = "membership_service_error"

    def __init__(self, errors: Sequence[ServiceError]):
        self.errors: tuple[ServiceError, ...] = tuple(errors)
        super().__init__([e.message for e in self.errors])

    @classmethod
    def single(cls, message: str, *, code: str = "", field=None, row_index=None) -> "MembershipServiceError":
        """One-message shorthand; defaults ``code`` to the subclass's own."""
        return cls([ServiceError(message, code or cls.default_code, field, row_index)])


class ActorNotAuthorized(MembershipServiceError):
    """Actor may not add/change memberships in the target tenant."""

    default_code = "actor_not_authorized"


class CrossTenantObject(MembershipServiceError):
    """A role, tenant, or tenant group outside the principal tenant's reach."""

    default_code = "cross_tenant_object"


class EscalationDenied(MembershipServiceError):
    """``core.auth.guards`` refused the grant."""

    default_code = "escalation_denied"


class ElevatedGrantIncomplete(MembershipServiceError):
    """Privileged role without a reason and/or a future expiry."""

    default_code = "elevated_grant_incomplete"


class AmbiguousIdentity(MembershipServiceError):
    """More than one account shares the submitted email."""

    default_code = "ambiguous_identity"


class DuplicateMembership(MembershipServiceError):
    """The user already belongs to this tenant.

    REVEALING BY CONSTRUCTION. This message discloses membership state for the
    target tenant, so it may only ever be produced for an actor who has already
    passed ``authorize_membership_write`` (INV-12).
    """

    default_code = "duplicate_membership"


class ConcurrentGrantChange(MembershipServiceError):
    """Grant state moved after validation because an unmigrated writer did not
    take the membership lock. Fail closed and ask the caller to resubmit."""

    default_code = "concurrent_grant_change"
