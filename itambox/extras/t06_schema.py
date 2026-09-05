"""Pure helpers for the T06 definition-schema transition.

The migration imports these functions without importing Django models. Keeping the
preflight rules pure makes the blocking classifications deterministic and easy to
exercise without a database.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


class T06SchemaConflict(RuntimeError):
    """Raised when predecessor data cannot be migrated without guessing."""


_MAX_TRANSITION_POSITION = 1_000_000_064


def validate_object_types(object_types: Iterable[str]) -> tuple[str, ...]:
    """Require at least one resolvable generic owner without dropping duplicates."""
    normalized = tuple(dict.fromkeys(str(value) for value in object_types))
    if not normalized:
        raise T06SchemaConflict("issue479:empty_object_types")
    return normalized


def validate_activation(activation: str) -> str:
    if activation not in {"composed", "global"}:
        raise T06SchemaConflict("issue479:invalid_activation")
    return activation


def classify_activation(*, has_memberships: bool) -> str:
    """Classify the one-time initial activation from the existing graph."""
    return "composed" if has_memberships else "global"


def normalize_lifecycle(*, lifecycle: str, deleted_at, deprecated_at, migration_timestamp: datetime):
    """Convert legacy deleted state to the permanent deprecated lifecycle."""
    if lifecycle not in {"active", "deprecated", "deleted"}:
        raise T06SchemaConflict("issue479:invalid_lifecycle")
    if deleted_at is not None:
        return "deprecated", deprecated_at or deleted_at
    if lifecycle == "deleted":
        return "deprecated", deprecated_at or migration_timestamp
    return lifecycle, deprecated_at


def dense_ordinals(rows: Iterable[tuple[int, str]]) -> dict[str, int]:
    """Return stable dense ordinals for ``(old_position, member_identity)`` rows."""
    materialized = list(rows)
    identities = [identity for _, identity in materialized]
    if len(identities) != len(set(identities)):
        raise T06SchemaConflict("issue479:duplicate_member")
    for position, _ in materialized:
        if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= _MAX_TRANSITION_POSITION:
            raise T06SchemaConflict("issue479:invalid_position")
    return {
        identity: ordinal
        for ordinal, (_, identity) in enumerate(sorted(materialized, key=lambda row: (row[0], row[1])), start=1)
    }
