"""
Pure configuration-contract helpers (issue #439).

Small, secret-free, reusable predicates and parsers for the production
configuration contract:

* ``validate_secret_key`` — full Django ``security.W009`` parity
  (>= 50 characters, >= 5 distinct characters, no ``django-insecure-``
  prefix), identifying exactly one failed rule per rejection;
* ``parse_api_token_peppers`` — tri-state (``unset`` / ``valid`` /
  ``malformed``) parsing of ``ITAMBOX_API_TOKEN_PEPPERS`` with
  rotation-ID collision guards;
* ``parse_field_encryption_keys`` — tri-state parsing of the comma-separated
  Fernet keyring ``ITAMBOX_FIELD_ENCRYPTION_KEYS``;
* ``validate_db_password`` — the production "explicitly configured" contract.

Design rules enforced here:

* diagnostics never contain, interpolate, hash, partially reveal, or log the
  configured secret values — rotation IDs and key indexes are the only
  identifying details ever named;
* ``unset`` and ``malformed`` are distinct states: only ``unset``/blank may
  use the warned SECRET_KEY-derived compatibility fallback; explicitly
  supplied malformed material must fail before traffic (prod settings raise
  ``ImproperlyConfigured`` at import);
* an explicitly supplied value that contains only separators or whitespace is
  malformed configuration, not an accidental fallback.

The module deliberately imports no Django so base settings, production
settings, ``core.crypto``, system checks, and tests can all consume it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cryptography.fernet import Fernet

# Django's deployment check (security.W009) thresholds, mirrored exactly.
SECRET_KEY_MIN_LENGTH = 50
SECRET_KEY_MIN_DISTINCT_CHARS = 5
SECRET_KEY_FORBIDDEN_PREFIX = "django-insecure-"

# Documented minimum length for dedicated pepper secrets.
PEPPER_MIN_SECRET_LENGTH = 50

SECRET_KEY_RULE_DIAGNOSTICS = {
    "missing": "is missing (set ITAMBOX_SECRET_KEY to a secure random value)",
    "forbidden_prefix": f"must not start with {SECRET_KEY_FORBIDDEN_PREFIX!r}",
    "too_short": f"must be at least {SECRET_KEY_MIN_LENGTH} characters",
    "too_few_distinct_chars": f"must contain at least {SECRET_KEY_MIN_DISTINCT_CHARS} distinct characters",
}


class ConfigState(str, Enum):
    """Tri-state classification for production-optional secret settings."""

    UNSET = "unset"
    VALID = "valid"
    MALFORMED = "malformed"


# ---------------------------------------------------------------------------
# SECRET_KEY — Django security.W009 parity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretKeyResult:
    """Outcome of the production SECRET_KEY predicate.

    ``failed_rule`` names exactly one rule (``missing``, ``forbidden_prefix``,
    ``too_short``, ``too_few_distinct_chars``) and never the key material.
    """

    valid: bool
    failed_rule: str | None = None


def validate_secret_key(value: str | None) -> SecretKeyResult:
    """
    Reject production-unfit SECRET_KEY values with Django's exact W009 rules.

    Rule evaluation order: missing, forbidden ``django-insecure-`` prefix,
    minimum length, minimum distinct-character count. The classification is
    identical to ``django.core.checks.security.base._check_secret_key``; the
    rule identification is repository-owned so the diagnostic can name the
    failed rule without depending on a private Django helper.
    """
    if value is None:
        return SecretKeyResult(False, "missing")
    if value.startswith(SECRET_KEY_FORBIDDEN_PREFIX):
        return SecretKeyResult(False, "forbidden_prefix")
    if len(value) < SECRET_KEY_MIN_LENGTH:
        return SecretKeyResult(False, "too_short")
    if len(set(value)) < SECRET_KEY_MIN_DISTINCT_CHARS:
        return SecretKeyResult(False, "too_few_distinct_chars")
    return SecretKeyResult(True)


# ---------------------------------------------------------------------------
# API-token peppers — unset vs valid vs malformed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PepperKeyring:
    """Parsed ``ITAMBOX_API_TOKEN_PEPPERS``.

    ``peppers`` is ``{int rotation id: secret}`` and non-empty only in the
    ``VALID`` state. ``error`` is secret-free (rotation IDs never are secrets).
    """

    state: ConfigState
    peppers: dict[int, str] = field(default_factory=dict)
    error: str | None = None


def _object_pairs(pairs: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """object_pairs_hook that preserves duplicate raw keys for collision checks."""
    return pairs


def parse_api_token_peppers(raw: str | None) -> PepperKeyring:
    """
    Parse the configured pepper mapping with an explicit tri-state result.

    Unset/blank keeps the SECRET_KEY-derived compatibility fallback (warned in
    production); explicitly malformed material returns ``MALFORMED`` with a
    secret-free error so production settings can refuse to import instead of
    silently downgrading to ``{}``.

    Valid mappings are non-empty JSON objects whose keys are canonical
    positive integer strings (no leading zeros, no duplicate raw keys, no
    normalization collisions) and whose values are non-empty strings of at
    least ``PEPPER_MIN_SECRET_LENGTH`` characters.
    """
    if raw is None or not raw.strip():
        return PepperKeyring(ConfigState.UNSET)

    pairs = _decode_pepper_object(raw)
    if pairs is None:
        return PepperKeyring(ConfigState.MALFORMED, error="must be a valid JSON object")
    if not pairs:
        return PepperKeyring(
            ConfigState.MALFORMED,
            error="must not be empty when explicitly configured",
        )

    peppers: dict[int, str] = {}
    for raw_id, secret in pairs:
        rotation_id = _validate_pepper_id(raw_id)
        if rotation_id is None:
            return PepperKeyring(ConfigState.MALFORMED, error=_pepper_id_error(raw_id))
        if rotation_id in peppers:
            return PepperKeyring(
                ConfigState.MALFORMED,
                error=f"duplicate rotation id {rotation_id}",
            )
        secret_error = _validate_pepper_secret(rotation_id, secret)
        if secret_error:
            return PepperKeyring(ConfigState.MALFORMED, error=secret_error)
        peppers[rotation_id] = secret

    return PepperKeyring(ConfigState.VALID, peppers)


def _decode_pepper_object(raw: str) -> list[tuple[str, Any]] | None:
    """
    Decode the raw value as a JSON object, preserving duplicate raw keys.

    ``json.loads`` with ``object_pairs_hook`` returns a list of key/value pairs
    for JSON objects; lists, strings, numbers, booleans, and null stay as-is.
    Returns ``None`` when the value is not a JSON object.
    """
    try:
        parsed = json.loads(raw, object_pairs_hook=_object_pairs)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or (
        parsed and not all(isinstance(pair, tuple) and len(pair) == 2 for pair in parsed)
    ):
        return None
    return parsed


def _validate_pepper_id(raw_id: Any) -> int | None:
    """The canonical positive-integer rotation id, or None when malformed."""
    if not isinstance(raw_id, str) or not raw_id.isdigit():
        return None
    rotation_id = int(raw_id)
    if rotation_id <= 0 or str(rotation_id) != raw_id:
        return None
    return rotation_id


def _pepper_id_error(raw_id: Any) -> str:
    """Secret-free diagnostic for a rejected rotation id."""
    if not isinstance(raw_id, str) or not raw_id.isdigit():
        return "every rotation id must be a positive integer string"
    rotation_id = int(raw_id)
    if rotation_id <= 0:
        return f"rotation id {raw_id!r} must be a positive integer"
    return f"rotation id {raw_id!r} must be written without leading zeros"


def _validate_pepper_secret(rotation_id: int, secret: Any) -> str | None:
    """Secret-free diagnostic for a rejected pepper value, or None when valid."""
    if not isinstance(secret, str):
        return f"pepper for rotation id {rotation_id} must be a string"
    if len(secret) < PEPPER_MIN_SECRET_LENGTH:
        return f"pepper for rotation id {rotation_id} must be at least {PEPPER_MIN_SECRET_LENGTH} characters"
    return None


# ---------------------------------------------------------------------------
# Field-encryption keyring — unset vs valid vs malformed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldKeyring:
    """Parsed ``ITAMBOX_FIELD_ENCRYPTION_KEYS``.

    ``keys`` preserves the configured order (first key encrypts, all decrypt)
    and is non-empty only in the ``VALID`` state. ``error`` names the failing
    key index, never the key value.
    """

    state: ConfigState
    keys: tuple[str, ...] = ()
    error: str | None = None


def parse_field_encryption_keys(raw: str | None) -> FieldKeyring:
    """
    Parse the comma-separated Fernet keyring with an explicit tri-state result.

    Unset/blank keeps the SECRET_KEY-derived compatibility fallback (warned in
    production). An explicitly supplied non-empty value that contains only
    separators or whitespace is ``MALFORMED`` — an accidental fallback is not
    a supported state. Every configured key is validated with ``Fernet()``;
    the diagnostic identifies only the failing key's index.
    """
    if raw is None or raw == "":
        return FieldKeyring(ConfigState.UNSET)

    entries = [entry.strip() for entry in raw.split(",")]
    non_empty = [entry for entry in entries if entry]
    if not non_empty:
        return FieldKeyring(
            ConfigState.MALFORMED,
            error="contains no keys (only separators or whitespace were supplied)",
        )
    for index, key in enumerate(non_empty, start=1):
        try:
            Fernet(key)
        except (TypeError, ValueError):
            return FieldKeyring(
                ConfigState.MALFORMED,
                error=(
                    f"invalid Fernet key at index {index}; configured keys must be urlsafe base64-encoded 32-byte keys"
                ),
            )
    return FieldKeyring(ConfigState.VALID, tuple(non_empty))


# ---------------------------------------------------------------------------
# Database password — the production "explicitly configured" contract
# ---------------------------------------------------------------------------


def validate_db_password(raw: str | None) -> bool:
    """
    True only when an explicitly supplied, non-empty database password exists.

    The bundled Compose stack and the production settings both fail closed on
    this predicate. "Explicitly configured" is the contract: the literal value
    is deliberately not inspected, so an external operator may configure any
    password (even one resembling the old development default).
    """
    return bool(raw is not None and raw.strip())
