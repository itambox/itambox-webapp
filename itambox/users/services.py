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

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

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
    return User.objects.normalize_email((email or '').strip())


def resolve_existing_user(email):
    """Return the single account whose email matches ``email`` case-insensitively,
    or ``None``. Raises :class:`AmbiguousEmailError` if more than one matches —
    never silently selects the lowest-PK row."""
    normalized = normalize_email(email)
    if not normalized:
        return None
    matches = list(User.objects.filter(email__iexact=normalized).order_by('pk')[:2])
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
    max_len = User._meta.get_field('username').max_length
    if len(email) <= max_len and not User.objects.filter(username=email).exists():
        return email
    digest = hashlib.sha256(email.encode('utf-8')).hexdigest()[:12]
    prefix = email[:max_len - len(digest) - 1].rstrip('-') or 'user'
    candidate = f"{prefix}-{digest}"[:max_len]
    unique = candidate
    n = 0
    while User.objects.filter(username=unique).exists():
        n += 1
        suffix = f"-{n}"
        unique = f"{candidate[:max_len - len(suffix)]}{suffix}"
    return unique


def resolve_or_create_user(*, email, first_name='', last_name=''):
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
                first_name=(first_name or '').strip(),
                last_name=(last_name or '').strip(),
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
