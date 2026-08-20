"""Identity resolution/creation service (RBAC inline "new user" onboarding).

The membership "Add member" flow can create a user inline from an email. The
naive get-then-insert was not transaction-safe, silently picked the lowest-PK
account when an email was duplicated, and copied a (up-to-254-char) email straight
into the 150-char ``username`` field. This module centralises the rules so the
form only validates intent and delegates the write:

  * email is normalised once with the user model's own rules;
  * more than one account for an email is AMBIGUOUS — fail closed, never pick one;
  * exactly one match is reused as-is (profile fields are never overwritten);
  * no match creates an account inside a transaction, race-safe (a concurrent
    creation is caught and re-resolved), with an unusable password and a username
    that fits the model's ``max_length`` (the normalised email when it fits and is
    free, otherwise a deterministic collision-resistant handle) while the full
    email is preserved in ``User.email``.
"""

import hashlib
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from .models import UserPreference

User = get_user_model()


class AmbiguousEmailError(Exception):
    """Raised when more than one account shares an email — the caller must not
    silently pick one. Email is deliberately NOT globally unique in this model
    (SSO/SCIM/LDAP and the importer provision accounts independently of email, and
    a hard constraint would break them or invite email-based account-linking
    takeover), so ambiguity is rejected here, at the write path, instead."""

    def __init__(self, email):
        self.email = email
        super().__init__(f"Multiple accounts share the email {email!r}.")


def normalize_email(email):
    """Normalise ``email`` with the user manager's rules (lower-cases the domain)."""
    return User.objects.normalize_email((email or "").strip())


def resolve_existing_user(email):
    """Return the single account whose email matches ``email`` case-insensitively,
    or ``None``. Raises :class:`AmbiguousEmailError` if more than one matches —
    never silently selects the lowest-PK row."""
    normalized = normalize_email(email)
    if not normalized:
        return None
    matches = list(User.objects.filter(email__iexact=normalized).order_by("pk")[:2])
    if len(matches) > 1:
        raise AmbiguousEmailError(normalized)
    return matches[0] if matches else None


def _fitting_username(email):
    """A username for ``email`` that fits ``username.max_length`` and is free.

    Uses the email verbatim when it fits and is available; otherwise a
    deterministic ``<prefix>-<sha256[:12]>`` handle (same email → same handle, so
    concurrent creates collide and one loses the race rather than duplicating),
    disambiguated with a numeric suffix in the vanishingly unlikely digest clash.
    """
    max_len = User._meta.get_field("username").max_length
    if len(email) <= max_len and not User.objects.filter(username=email).exists():
        return email
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
    prefix = email[: max_len - len(digest) - 1].rstrip("-") or "user"
    candidate = f"{prefix}-{digest}"[:max_len]
    unique = candidate
    n = 0
    while User.objects.filter(username=unique).exists():
        n += 1
        suffix = f"-{n}"
        unique = f"{candidate[: max_len - len(suffix)]}{suffix}"
    return unique


def resolve_or_create_user(*, email, first_name="", last_name=""):
    """Get-or-create an account for ``email`` (case-insensitive), transaction-safe.

    Returns ``(user, created)``. An existing account is reused WITHOUT overwriting
    its profile. A new account gets an unusable password (credentials are issued
    later via the membership detail's "send setup link" action) and a length-safe
    username; the full email is stored in ``User.email``. Raises
    :class:`AmbiguousEmailError` when the email matches more than one account.
    """
    normalized = normalize_email(email)
    existing = resolve_existing_user(normalized)
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            user = User(
                username=_fitting_username(normalized),
                email=normalized,
                first_name=(first_name or "").strip(),
                last_name=(last_name or "").strip(),
                is_active=True,
            )
            user.set_unusable_password()
            user.save()
        return user, True
    except IntegrityError:
        # A concurrent request created the account first and won the race on the
        # deterministic username (same email → same candidate handle): re-resolve
        # and reuse rather than surfacing the collision.
        again = resolve_existing_user(normalized)
        if again is not None:
            return again, False
        raise


DEFAULT_WORKSPACE_KEY = "default_workspace"
WORKSPACE_AUTOMATIC = ""
WORKSPACE_ALL = "all"
WORKSPACE_TENANT_PREFIX = "tenant:"
WORKSPACE_GROUP_PREFIX = "group:"
WORKSPACE_SESSION_KEYS = (
    "active_tenant_id",
    "active_tenant_group_id",
    "active_all_accessible",
)


@dataclass(frozen=True)
class WorkspaceSelection:
    tenant: object | None = None
    group: object | None = None
    all_accessible: bool = False


def parse_workspace_key(value):
    """Return ``(kind, primary-key)`` for a stored workspace key.

    The empty value means that no personal default is configured. Invalid values
    are rejected rather than being interpreted as a tenant identifier.
    """
    if value == WORKSPACE_ALL:
        return WORKSPACE_ALL, None
    if not isinstance(value, str):
        return None
    for prefix, kind in (
        (WORKSPACE_TENANT_PREFIX, "tenant"),
        (WORKSPACE_GROUP_PREFIX, "group"),
    ):
        if not value.startswith(prefix):
            continue
        raw_id = value[len(prefix) :]
        if raw_id.isdecimal() and int(raw_id) > 0:
            return kind, int(raw_id)
        return None
    return None


def clear_workspace_session(session):
    for key in WORKSPACE_SESSION_KEYS:
        session.pop(key, None)


def resolve_default_workspace(user):
    """Resolve the user's stored default without widening current access."""
    data = UserPreference.objects.filter(user=user).values_list("data", flat=True).first()
    if not isinstance(data, dict):
        return None
    return resolve_workspace_selection(user, data.get(DEFAULT_WORKSPACE_KEY))


def _accessible_tenant_ids(user):
    # inline imports: app-registry: resolve organization models after Django apps load
    from organization.access import accessible_tenant_ids

    if user is not None and getattr(user, "is_superuser", False):
        return None
    return accessible_tenant_ids(user)


def _accessible_group_ids(user, accessible_ids=None):
    # inline imports: app-registry: resolve organization models after Django apps load
    from organization.access import get_ancestor_tenant_group_ids
    from organization.models import Tenant

    if accessible_ids is None:
        accessible_ids = _accessible_tenant_ids(user)
    if accessible_ids is None:
        return set()

    own_group_ids = set(
        Tenant._base_manager.filter(
            pk__in=accessible_ids,
            deleted_at__isnull=True,
        )
        .exclude(group_id__isnull=True)
        .values_list("group_id", flat=True)
    )
    group_ids = set()
    for group_id in own_group_ids:
        group_ids.update(get_ancestor_tenant_group_ids(group_id, live_only=True))
    return group_ids


def workspace_choices(user):
    """Return choices valid for ``user`` at the time the form is rendered."""
    # inline imports: app-registry: resolve organization models after Django apps load
    from organization.models import Tenant, TenantGroup

    choices = [(WORKSPACE_AUTOMATIC, _("Automatic"))]
    accessible_ids = _accessible_tenant_ids(user)
    if accessible_ids is None:
        tenants = Tenant._base_manager.filter(deleted_at__isnull=True).order_by("name")
        groups = TenantGroup._base_manager.filter(deleted_at__isnull=True).order_by("name")
    else:
        if not accessible_ids:
            return choices
        tenants = Tenant._base_manager.filter(
            pk__in=accessible_ids,
            deleted_at__isnull=True,
        ).order_by("name")
        group_ids = _accessible_group_ids(user, accessible_ids)
        groups = TenantGroup._base_manager.filter(
            pk__in=group_ids,
            deleted_at__isnull=True,
        ).order_by("name")

    choices.append((WORKSPACE_ALL, _("All Tenants")))
    choices.extend((f"{WORKSPACE_GROUP_PREFIX}{group.pk}", str(group)) for group in groups)
    choices.extend((f"{WORKSPACE_TENANT_PREFIX}{tenant.pk}", str(tenant)) for tenant in tenants)
    return choices


def _resolve_all_workspace(accessible_ids):
    if accessible_ids is None:
        return WorkspaceSelection()
    if accessible_ids:
        return WorkspaceSelection(all_accessible=True)
    return None


def _resolve_tenant_workspace(object_id, accessible_ids):
    # inline imports: app-registry: resolve organization models after Django apps load
    from organization.models import Tenant

    if accessible_ids is not None and object_id not in accessible_ids:
        return None
    tenant = Tenant._base_manager.filter(
        pk=object_id,
        deleted_at__isnull=True,
    ).first()
    return WorkspaceSelection(tenant=tenant) if tenant is not None else None


def _resolve_group_workspace(object_id, accessible_ids):
    # inline imports: app-registry: resolve organization models after Django apps load
    from organization.access import get_descendant_tenant_group_ids
    from organization.models import Tenant, TenantGroup

    group = TenantGroup._base_manager.filter(
        pk=object_id,
        deleted_at__isnull=True,
    ).first()
    if group is None:
        return None
    if accessible_ids is None:
        return WorkspaceSelection(group=group)

    descendant_group_ids = get_descendant_tenant_group_ids(group.pk, live_only=True)
    group_tenant_ids = set(
        Tenant._base_manager.filter(
            group_id__in=descendant_group_ids,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    if accessible_ids & group_tenant_ids:
        return WorkspaceSelection(group=group)
    return None


def resolve_workspace_selection(user, value):
    """Resolve a stored workspace key only when it is currently authorized."""
    parsed = parse_workspace_key(value)
    if parsed is None:
        return None
    kind, object_id = parsed
    accessible_ids = _accessible_tenant_ids(user)
    if kind == WORKSPACE_ALL:
        return _resolve_all_workspace(accessible_ids)
    if kind == "tenant":
        return _resolve_tenant_workspace(object_id, accessible_ids)
    if kind == "group":
        return _resolve_group_workspace(object_id, accessible_ids)
    return None
