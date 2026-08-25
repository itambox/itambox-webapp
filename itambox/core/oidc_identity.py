from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from django.core.exceptions import ValidationError

OIDC_ISSUER_MAX_LENGTH = 2000
OIDC_SUBJECT_MAX_LENGTH = 255

# OIDC provisioning may touch ordinary User/Organization models whose normal
# audit representations contain mutable profile identifiers. Keep that audit
# useful while preventing the external identity and profile-linking material
# from reaching ObjectChange snapshots or object representations.
OIDC_AUDIT_EXCLUDED_FIELDS = frozenset(
    {
        "access_token",
        "claims",
        "client_secret",
        "email",
        "groups",
        "id_token",
        "issuer",
        "last_name",
        "password",
        "secret",
        "subject",
        "token",
        "upn",
        "username",
        "first_name",
    }
)

_oidc_sensitive_audit = ContextVar("oidc_sensitive_audit", default=False)


def validate_oidc_issuer(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > OIDC_ISSUER_MAX_LENGTH:
        raise ValidationError("OIDC issuer is invalid.", code="invalid_oidc_issuer")
    return value


def validate_oidc_subject(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > OIDC_SUBJECT_MAX_LENGTH or not value.isascii():
        raise ValidationError("OIDC subject is invalid.", code="invalid_oidc_subject")
    return value


def validate_oidc_identity(issuer: object, subject: object) -> tuple[str, str]:
    return validate_oidc_issuer(issuer), validate_oidc_subject(subject)


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(8, byteorder="big", signed=False) + encoded


def oidc_identity_bytes(issuer: str, subject: str) -> bytes:
    canonical_issuer, canonical_subject = validate_oidc_identity(issuer, subject)
    return b"itambox-oidc-identity-lock-v1\x00" + _frame(canonical_issuer) + _frame(canonical_subject)


def oidc_identity_digest(issuer: str, subject: str) -> bytes:
    return hashlib.sha256(oidc_identity_bytes(issuer, subject)).digest()


def oidc_advisory_lock_parts(issuer: str, subject: str) -> tuple[int, int]:
    digest = oidc_identity_digest(issuer, subject)
    return (
        int.from_bytes(digest[0:4], byteorder="big", signed=True),
        int.from_bytes(digest[4:8], byteorder="big", signed=True),
    )


def oidc_advisory_lock_key(issuer: str, subject: str) -> int:
    """Return the signed 64-bit form retained for helper compatibility."""

    return int.from_bytes(oidc_identity_digest(issuer, subject)[:8], byteorder="big", signed=True)


def oidc_audit_excluded_fields(fields: list[str] | tuple[str, ...]) -> list[str]:
    excluded = list(fields)
    if not oidc_sensitive_audit_enabled():
        return excluded
    for field in OIDC_AUDIT_EXCLUDED_FIELDS:
        if field not in excluded:
            excluded.append(field)
    return excluded


def oidc_sensitive_audit_enabled() -> bool:
    return _oidc_sensitive_audit.get()


@contextmanager
def oidc_sensitive_audit() -> Iterator[None]:
    token = _oidc_sensitive_audit.set(True)
    try:
        yield
    finally:
        _oidc_sensitive_audit.reset(token)
