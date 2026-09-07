"""Pure helpers for explicit historical-key selection and state binding."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

_HISTORICAL_REASON_CODES = frozenset({"INACTIVE_COMPOSITION", "DEPRECATED_FIELD", "DEPRECATED_CHOICE"})


def normalize_history_keys(keys: object) -> tuple[str, ...]:
    """Validate and deterministically order an explicit history-key selection."""
    if type(keys) is not tuple:
        raise TypeError("history keys must be a tuple")
    normalized: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if type(key) is not str or not key:
            raise ValueError("history keys must contain non-empty strings")
        if key in seen:
            raise ValueError(f"duplicate history key: {key}")
        seen.add(key)
        normalized.append(key)
    return tuple(sorted(normalized))


def _typed_json_value(value: object) -> object:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["boolean", value]
    if type(value) is int:
        return ["integer", value]
    if type(value) is float:
        return ["number", value]
    if type(value) is str:
        return ["string", value]
    if isinstance(value, Mapping):
        members: list[list[object]] = []
        for key, nested in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be strings")
            members.append([key, _typed_json_value(nested)])
        members.sort(key=lambda item: item[0])
        return ["object", members]
    if type(value) in {list, tuple}:
        return ["array", [_typed_json_value(nested) for nested in value]]
    raise TypeError("history state contains an unsupported JSON value")


def history_state_digest(keys: object, stored_values: Mapping[str, object]) -> str:
    """Return a type-preserving digest for the selected raw storage entries."""
    normalized = normalize_history_keys(keys)
    entries = []
    for key in normalized:
        if key in stored_values:
            value = _typed_json_value(stored_values[key])
        else:
            value = ["missing"]
        entries.append({"key": key, "value": value})
    payload = {"version": 1, "entries": entries}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def is_history_cleanup_eligible(entry: object) -> bool:
    """Accept unknown entries and any adopted historical projection reason."""
    if getattr(entry, "state", None) == "unknown":
        return True
    reasons = set(getattr(entry, "reason_codes", ()))
    return bool(reasons.intersection(_HISTORICAL_REASON_CODES))


__all__ = [
    "history_state_digest",
    "is_history_cleanup_eligible",
    "normalize_history_keys",
]
