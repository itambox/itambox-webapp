"""Request-scoped runtime context: the contextvars every layer reads.

This module is the designated *leaf* of the request-context dependency graph.
It owns the ``ContextVar`` objects (and their accessors) that carry the active
tenant / tenant group / membership / scope flag, the acting user, and the
request id. Nothing here imports another first-party module, so any layer —
managers, middleware, auth backends, MFA policy, background tasks — can import
it at module scope without closing an import loop.

Why it exists (issue #87, phase D): the tenant scoping managers, the middleware
that populates the context, and the auth backends that read it all needed the
*same* contextvars, and each half used to import the other half's module. That
is a genuine cycle, and it was only being hidden by function-body imports. A
deferred import hides a cycle; it does not remove one. Owning the shared
constructs here removes it.

``core.managers`` and ``itambox.middleware`` re-export these names, so the
long-standing import sites (``from core.managers import set_current_tenant``,
``from itambox.middleware import get_current_user``) keep working unchanged.

Only pure-Python state belongs here. Anything that touches models, settings,
the cache, or the app registry belongs in the module that owns that concern —
adding such a dependency would re-create the cycle this module exists to break.
"""

import contextvars
from typing import Any, Optional

_current_tenant = contextvars.ContextVar("current_tenant", default=None)
_current_tenant_group = contextvars.ContextVar("current_tenant_group", default=None)
_current_membership = contextvars.ContextVar("current_membership", default=None)
# "All accessible tenants" scope for a non-superuser: no single tenant/group is
# active, yet the request is NOT global — it is scoped to exactly the tenants the
# canonical resolver authorizes (issue #29). Distinct from the superuser global
# scope (all three None + is_superuser) so it can never widen into it.
_current_all_accessible = contextvars.ContextVar("current_all_accessible", default=False)
_descendant_group_ids_cache = contextvars.ContextVar("descendant_group_ids_cache", default=None)
_current_user = contextvars.ContextVar("current_user", default=None)
_request_id = contextvars.ContextVar("request_id", default=None)


def set_current_tenant(tenant: Optional[Any]) -> None:
    _current_tenant.set(tenant)
    _descendant_group_ids_cache.set(None)


def get_current_tenant() -> Optional[Any]:
    return _current_tenant.get()


def set_current_tenant_group(group: Optional[Any]) -> None:
    _current_tenant_group.set(group)
    _descendant_group_ids_cache.set(None)


def get_current_tenant_group() -> Optional[Any]:
    return _current_tenant_group.get()


def set_current_membership(membership: Optional[Any]) -> None:
    _current_membership.set(membership)
    _descendant_group_ids_cache.set(None)


def get_current_membership() -> Optional[Any]:
    return _current_membership.get()


def set_current_all_accessible(flag: bool) -> None:
    _current_all_accessible.set(bool(flag))
    _descendant_group_ids_cache.set(None)


def get_current_all_accessible() -> bool:
    return _current_all_accessible.get()


def get_current_request_id():
    return _request_id.get()


def get_current_user():
    return _current_user.get()


def set_current_user(user):
    """Bind the current-user contextvar after the fact.

    DRF authentication runs inside a view's ``initial()`` — *after*
    ``CurrentUserMiddleware`` has already captured ``request.user`` (which is
    ``AnonymousUser`` for a token-authenticated request at that point). Token-auth
    views (e.g. SCIM) call this once authenticated so changelog rows are attributed
    to the acting principal instead of being recorded as ``user=None`` ('System').
    The middleware's response phase resets the contextvar via its entry token, so
    this set is correctly torn down at request end (no cross-request leak).
    """
    _current_user.set(user)


def get_current_scope_conflict(user: Optional[Any]) -> bool:
    """True when more than one of tenant / group / all-accessible scope is
    active for an authenticated non-superuser.

    The session/middleware resolution, token authentication, and TaskContext
    each set at most one of these by construction, so a contradiction here
    means the contextvars were poked directly (a bug, or a background task
    inheriting stale ambient state from a wrapping request). Tenant-scoping
    consumers must fail closed to nothing in that case rather than silently
    prioritize one of the contradictory states — a superuser has no such
    ambiguity (they keep their own global/explicit-scope path regardless).
    """
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return False
    active_states = (
        get_current_tenant(),
        get_current_tenant_group(),
        get_current_all_accessible(),
    )
    return sum(bool(state) for state in active_states) > 1
