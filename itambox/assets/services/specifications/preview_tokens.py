"""Stateless signed preview-token support for specification commands.

This module deliberately owns token integrity and claim binding only.  It does
not authenticate actors, resolve access scopes, load owners, acquire locks, or
make a signed token an authorization decision.  Callers must obtain current
authentication and authorization, including the Organization-owned scope
fingerprint, before invoking this module.

The ``access_scope_fingerprint`` claim is the opaque Organization-produced
binding of the requested selector, authorization revision, and authorized
scope.  Keeping it opaque here avoids creating a second authorization
authority while still binding a preview to both sides of that decision.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from django.core import signing

PREVIEW_TOKEN_SALT = "assets.services.specifications.preview_tokens.v1"
PREVIEW_TOKEN_PURPOSE = "asset-specification-preview"
PREVIEW_TOKEN_FORMAT_VERSION = 1
PREVIEW_TOKEN_LIFETIME_SECONDS = 30 * 60
MAX_PREVIEW_TOKEN_LENGTH = 8192
MAX_NORMALIZED_INPUT_BYTES = 10 * 1024 * 1024
MAX_NORMALIZED_INPUT_DEPTH = 16
MAX_NORMALIZED_INPUT_ITEMS = 512
MAX_NORMALIZED_INPUT_NODES = 65536
MAX_NORMALIZED_INPUT_STRING_LENGTH = 4096
MAX_CLAIM_STRING_LENGTH = 512

OwnerKind: TypeAlias = Literal["asset_type", "asset", "category"]
ClockValue: TypeAlias = int | Callable[[], int]
SigningKey: TypeAlias = str | bytes


class PreviewTokenError(ValueError):
    """Stable, nondisclosing failure for every unusable preview token."""

    code = "STALE_PLAN"

    def __init__(self, reason: str = "invalid preview token") -> None:
        self.reason = reason
        super().__init__(self.code)


@dataclass(frozen=True)
class OwnerRef:
    """Internal immutable target identity copied from the frozen contract."""

    owner_kind: OwnerKind
    owner_id: int

    def __post_init__(self) -> None:
        if self.owner_kind not in {"asset_type", "asset", "category"}:
            raise ValueError("owner_kind is not supported")
        _require_positive_integer(self.owner_id, "owner_id")


@dataclass(frozen=True)
class PreviewTokenExpectation:
    """Expected, server-derived claims used when a token is replayed."""

    actor_id: int
    authentication_revision: str
    access_scope_fingerprint: str | None
    command_kind: str
    target: OwnerRef | None
    normalized_input_digest: str
    expected_resource_revision: str | None
    expected_definition_revision: str
    expected_category_default_snapshot_revision: str | None
    historical_state_digest: str | None

    def __post_init__(self) -> None:
        _require_positive_integer(self.actor_id, "actor_id")
        _require_claim_string(self.authentication_revision, "authentication_revision")
        _require_optional_claim_string(self.access_scope_fingerprint, "access_scope_fingerprint")
        _require_claim_string(self.command_kind, "command_kind")
        if self.target is not None and not isinstance(self.target, OwnerRef):
            raise TypeError("target must be an OwnerRef or None")
        _require_claim_string(self.normalized_input_digest, "normalized_input_digest")
        _require_optional_claim_string(self.expected_resource_revision, "expected_resource_revision")
        _require_claim_string(self.expected_definition_revision, "expected_definition_revision")
        _require_optional_claim_string(
            self.expected_category_default_snapshot_revision,
            "expected_category_default_snapshot_revision",
        )
        _require_optional_claim_string(self.historical_state_digest, "historical_state_digest")

    def with_times(self, issued_at_epoch_seconds: int) -> PreviewTokenClaims:
        issued_at = _require_epoch_seconds(issued_at_epoch_seconds, "issued_at_epoch_seconds")
        return PreviewTokenClaims(
            actor_id=self.actor_id,
            authentication_revision=self.authentication_revision,
            access_scope_fingerprint=self.access_scope_fingerprint,
            command_kind=self.command_kind,
            target=self.target,
            normalized_input_digest=self.normalized_input_digest,
            expected_resource_revision=self.expected_resource_revision,
            expected_definition_revision=self.expected_definition_revision,
            expected_category_default_snapshot_revision=self.expected_category_default_snapshot_revision,
            historical_state_digest=self.historical_state_digest,
            issued_at_epoch_seconds=issued_at,
            expires_at_epoch_seconds=issued_at + PREVIEW_TOKEN_LIFETIME_SECONDS,
        )


@dataclass(frozen=True)
class PreviewTokenClaims(PreviewTokenExpectation):
    """Internal immutable representation of the complete signed claim set."""

    issued_at_epoch_seconds: int
    expires_at_epoch_seconds: int

    def __post_init__(self) -> None:
        super().__post_init__()
        issued_at = _require_epoch_seconds(self.issued_at_epoch_seconds, "issued_at_epoch_seconds")
        expires_at = _require_epoch_seconds(self.expires_at_epoch_seconds, "expires_at_epoch_seconds")
        if expires_at - issued_at != PREVIEW_TOKEN_LIFETIME_SECONDS:
            raise ValueError("preview token lifetime must be exactly 30 minutes")

    def binding(self) -> PreviewTokenExpectation:
        """Return the non-time claims a later command must compare."""

        return PreviewTokenExpectation(
            actor_id=self.actor_id,
            authentication_revision=self.authentication_revision,
            access_scope_fingerprint=self.access_scope_fingerprint,
            command_kind=self.command_kind,
            target=self.target,
            normalized_input_digest=self.normalized_input_digest,
            expected_resource_revision=self.expected_resource_revision,
            expected_definition_revision=self.expected_definition_revision,
            expected_category_default_snapshot_revision=self.expected_category_default_snapshot_revision,
            historical_state_digest=self.historical_state_digest,
        )


def normalized_input_digest(value: object) -> str:
    """Hash bounded canonical JSON while preserving presence-sensitive input.

    Mapping key order is normalized, while omitted keys and explicit empty
    values remain different JSON structures.  The accepted value vocabulary is
    intentionally limited to the already-parsed JSON values used by the
    command DTOs; this function does not apply domain validation.
    """

    budget = [0]
    normalized = _normalize_json_value(value, depth=0, budget=budget)
    envelope = {"version": 1, "input": normalized}
    try:
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("normalized input cannot be canonicalized") from None
    if len(encoded) > MAX_NORMALIZED_INPUT_BYTES:
        raise ValueError("normalized input exceeds the size limit")
    return hashlib.sha256(encoded).hexdigest()


def issue_preview_token(
    expected: PreviewTokenExpectation,
    *,
    key: SigningKey,
    now: ClockValue | None = None,
) -> str:
    """Issue a 30-minute token from server-derived expected claims.

    ``key`` is required explicitly.  There is no settings, development-key,
    or test-key fallback, so a production caller must wire ordinary server
    settings into this interface without persisting or reporting the secret.
    """

    if not isinstance(expected, PreviewTokenExpectation):
        raise TypeError("expected must be a PreviewTokenExpectation")
    signer = _build_signer(key)
    claims = expected.with_times(_resolve_now(now))
    token = signer.sign_object(_claims_payload(claims), compress=False)
    if len(token) > MAX_PREVIEW_TOKEN_LENGTH:
        raise ValueError("preview token exceeds the size limit")
    return token


def verify_preview_token(
    token: str,
    *,
    expected: PreviewTokenExpectation,
    key: SigningKey,
    now: ClockValue | None = None,
) -> PreviewTokenClaims:
    """Verify integrity, time bounds, and all server-derived expected claims.

    Verification is read-only and stateless.  Every untrusted-token failure
    has the stable ``STALE_PLAN`` code and no token, claim, key, or owner detail
    is included in the exception text.
    """

    if not isinstance(expected, PreviewTokenExpectation):
        raise TypeError("expected must be a PreviewTokenExpectation")
    signer = _build_signer(key)
    _validate_token_text(token)
    try:
        payload = signer.unsign_object(token)
        claims = _claims_from_payload(payload)
    except (signing.BadSignature, ValueError, TypeError, RecursionError):
        raise PreviewTokenError("invalid_format_or_signature") from None

    current_time = _resolve_now(now)
    if current_time < claims.issued_at_epoch_seconds:
        raise PreviewTokenError("not_yet_valid")
    if current_time >= claims.expires_at_epoch_seconds:
        raise PreviewTokenError("expired")
    if claims.binding() != expected:
        raise PreviewTokenError("claim_mismatch")
    return claims


def _build_signer(key: SigningKey) -> signing.Signer:
    _validate_signing_key(key)
    # Passing an empty fallback tuple keeps this pure helper independent of
    # Django settings while retaining Django's approved HMAC signing primitive.
    return signing.Signer(key=key, salt=PREVIEW_TOKEN_SALT, fallback_keys=())


def _claims_payload(claims: PreviewTokenClaims) -> dict[str, object]:
    target: dict[str, object] | None = None
    if claims.target is not None:
        target = {
            "owner_kind": claims.target.owner_kind,
            "owner_id": claims.target.owner_id,
        }
    return {
        "version": PREVIEW_TOKEN_FORMAT_VERSION,
        "purpose": PREVIEW_TOKEN_PURPOSE,
        "claims": {
            "actor_id": claims.actor_id,
            "authentication_revision": claims.authentication_revision,
            "access_scope_fingerprint": claims.access_scope_fingerprint,
            "command_kind": claims.command_kind,
            "target": target,
            "normalized_input_digest": claims.normalized_input_digest,
            "expected_resource_revision": claims.expected_resource_revision,
            "expected_definition_revision": claims.expected_definition_revision,
            "expected_category_default_snapshot_revision": claims.expected_category_default_snapshot_revision,
            "historical_state_digest": claims.historical_state_digest,
            "issued_at_epoch_seconds": claims.issued_at_epoch_seconds,
            "expires_at_epoch_seconds": claims.expires_at_epoch_seconds,
        },
    }


def _claims_from_payload(payload: object) -> PreviewTokenClaims:
    if not isinstance(payload, dict) or set(payload) != {"version", "purpose", "claims"}:
        raise ValueError("preview token envelope is malformed")
    if not isinstance(payload["version"], int) or isinstance(payload["version"], bool):
        raise ValueError("preview token envelope version is malformed")
    if payload["version"] != PREVIEW_TOKEN_FORMAT_VERSION or payload["purpose"] != PREVIEW_TOKEN_PURPOSE:
        raise ValueError("preview token envelope is not current")

    raw_claims = payload["claims"]
    expected_keys = {
        "actor_id",
        "authentication_revision",
        "access_scope_fingerprint",
        "command_kind",
        "target",
        "normalized_input_digest",
        "expected_resource_revision",
        "expected_definition_revision",
        "expected_category_default_snapshot_revision",
        "historical_state_digest",
        "issued_at_epoch_seconds",
        "expires_at_epoch_seconds",
    }
    if not isinstance(raw_claims, dict) or set(raw_claims) != expected_keys:
        raise ValueError("preview token claims are malformed")

    raw_target = raw_claims["target"]
    if raw_target is None:
        target = None
    else:
        if not isinstance(raw_target, dict) or set(raw_target) != {"owner_kind", "owner_id"}:
            raise ValueError("preview token target is malformed")
        target = OwnerRef(owner_kind=raw_target["owner_kind"], owner_id=raw_target["owner_id"])

    return PreviewTokenClaims(
        actor_id=raw_claims["actor_id"],
        authentication_revision=raw_claims["authentication_revision"],
        access_scope_fingerprint=raw_claims["access_scope_fingerprint"],
        command_kind=raw_claims["command_kind"],
        target=target,
        normalized_input_digest=raw_claims["normalized_input_digest"],
        expected_resource_revision=raw_claims["expected_resource_revision"],
        expected_definition_revision=raw_claims["expected_definition_revision"],
        expected_category_default_snapshot_revision=raw_claims["expected_category_default_snapshot_revision"],
        historical_state_digest=raw_claims["historical_state_digest"],
        issued_at_epoch_seconds=raw_claims["issued_at_epoch_seconds"],
        expires_at_epoch_seconds=raw_claims["expires_at_epoch_seconds"],
    )


def _normalize_json_value(value: object, *, depth: int, budget: list[int]) -> object:
    if depth > MAX_NORMALIZED_INPUT_DEPTH:
        raise ValueError("normalized input is too deeply nested")
    budget[0] += 1
    if budget[0] > MAX_NORMALIZED_INPUT_NODES:
        raise ValueError("normalized input has too many values")
    if value is None or isinstance(value, (bool, str, int)):
        return _normalize_scalar(value)
    if isinstance(value, Mapping):
        return _normalize_mapping(value, depth=depth, budget=budget)
    if isinstance(value, (list, tuple)):
        return _normalize_sequence(value, depth=depth, budget=budget)
    raise ValueError("normalized input contains an unsupported value")


def _normalize_scalar(value: object) -> object:
    if isinstance(value, str) and len(value) > MAX_NORMALIZED_INPUT_STRING_LENGTH:
        raise ValueError("normalized input string is too long")
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 256:
        raise ValueError("normalized input integer is too large")
    return value


def _normalize_mapping(value: Mapping[object, object], *, depth: int, budget: list[int]) -> dict[str, object]:
    if len(value) > MAX_NORMALIZED_INPUT_ITEMS:
        raise ValueError("normalized input mapping is too large")
    normalized_mapping: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("normalized input mapping keys must be strings")
        if len(key) > MAX_NORMALIZED_INPUT_STRING_LENGTH:
            raise ValueError("normalized input mapping key is too long")
        normalized_mapping[key] = _normalize_json_value(item, depth=depth + 1, budget=budget)
    return normalized_mapping


def _normalize_sequence(value: list[object] | tuple[object, ...], *, depth: int, budget: list[int]) -> list[object]:
    if len(value) > MAX_NORMALIZED_INPUT_ITEMS:
        raise ValueError("normalized input sequence is too large")
    return [_normalize_json_value(item, depth=depth + 1, budget=budget) for item in value]


def _resolve_now(now: ClockValue | None) -> int:
    value = int(time.time()) if now is None else now() if callable(now) else now
    return _require_epoch_seconds(value, "current time")


def _require_positive_integer(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_epoch_seconds(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_claim_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > MAX_CLAIM_STRING_LENGTH or not value.isascii():
        raise ValueError(f"{name} is outside the bounded ASCII claim format")


def _require_optional_claim_string(value: object, name: str) -> None:
    if value is not None:
        _require_claim_string(value, name)


def _validate_signing_key(key: SigningKey) -> None:
    if not isinstance(key, (str, bytes)) or not key:
        raise ValueError("a non-empty signing key is required")


def _validate_token_text(token: object) -> None:
    if (
        not isinstance(token, str)
        or not token
        or len(token) > MAX_PREVIEW_TOKEN_LENGTH
        or not token.isascii()
        or token.startswith(".")
    ):
        raise PreviewTokenError("invalid_format")


__all__ = [
    "MAX_NORMALIZED_INPUT_BYTES",
    "MAX_PREVIEW_TOKEN_LENGTH",
    "PREVIEW_TOKEN_LIFETIME_SECONDS",
    "PREVIEW_TOKEN_PURPOSE",
    "PREVIEW_TOKEN_SALT",
    "OwnerRef",
    "PreviewTokenClaims",
    "PreviewTokenError",
    "PreviewTokenExpectation",
    "issue_preview_token",
    "normalized_input_digest",
    "verify_preview_token",
]
