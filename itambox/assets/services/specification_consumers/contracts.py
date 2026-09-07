"""Stable source-qualified contracts for Asset specification consumers.

The consumer layer persists identity as ``(source, field_key)``.  It never uses
translated labels, form names, table positions, or JSON paths as identifiers.
The contracts are deliberately pure so filters, reports, search, and exports
can share one boundary without importing request or model state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeAlias

TargetKind: TypeAlias = Literal["asset", "asset_type"]
ValueStatus: TypeAlias = Literal["current", "historical", "invalid", "unknown"]
SavedReferenceStatus: TypeAlias = Literal["current", "history", "invalid", "unknown"]
FilterOperator: TypeAlias = Literal[
    "eq",
    "neq",
    "contains",
    "contains_any",
    "contains_all",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_missing",
    "is_null",
    "is_empty",
]

_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_COLUMN_ID_RE = re.compile(r"^(asset|asset_type)\.spec\.([a-z][a-z0-9_]{0,63})$", re.ASCII)
_TARGET_KINDS = frozenset({"asset", "asset_type"})
_FILTER_OPERATORS = frozenset(
    {
        "eq",
        "neq",
        "contains",
        "contains_any",
        "contains_all",
        "gt",
        "gte",
        "lt",
        "lte",
        "is_missing",
        "is_null",
        "is_empty",
    }
)
_FILTER_STATUSES = frozenset({"current", "history", "invalid", "unknown"})
_MISSING: Final = object()
MISSING: Final = _MISSING


def _validate_json_filter_value(value: object) -> None:
    if value is MISSING:
        return
    if value is None or type(value) in {str, int, float, bool}:
        return
    if type(value) in {list, tuple}:
        if any(isinstance(item, Mapping) for item in value):
            raise ValueError("filter values cannot contain mappings")
        if any(isinstance(item, (list, tuple)) for item in value):
            raise ValueError("filter values must be JSON scalars or scalar sequences")
        return
    raise ValueError("filter values must be JSON scalars or scalar sequences")


@dataclass(frozen=True)
class FieldReference:
    """An immutable Asset/Asset Type specification identity."""

    source: TargetKind
    key: str

    def __post_init__(self) -> None:
        if self.source not in _TARGET_KINDS:
            raise ValueError("source must be 'asset' or 'asset_type'")
        if type(self.key) is not str or _FIELD_KEY_RE.fullmatch(self.key) is None:
            raise ValueError("field key must be a stable lowercase snake_case key")

    @property
    def column_id(self) -> str:
        return f"{self.source}.spec.{self.key}"

    @classmethod
    def from_column_id(cls, column_id: str) -> FieldReference:
        if type(column_id) is not str:
            raise ValueError("column id must be a string")
        match = _COLUMN_ID_RE.fullmatch(column_id)
        if match is None:
            raise ValueError("column id must use asset.spec.<key> or asset_type.spec.<key>")
        return cls(source=match.group(1), key=match.group(2))  # type: ignore[arg-type]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> FieldReference:
        if not isinstance(payload, Mapping):
            raise ValueError("field reference must be an object")
        forbidden = {"label", "field", "field_name", "json_path", "path", "column"}
        if forbidden.intersection(payload):
            raise ValueError("field references must not use labels, form names, or JSON paths")
        try:
            source = payload["source"]
            key = payload["field_key"]
        except KeyError as exc:
            raise ValueError("field reference requires source and field_key") from exc
        return cls(source=source, key=key)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, str]:
        return {"source": self.source, "field_key": self.key}


@dataclass(frozen=True)
class FieldFilter:
    """A source-qualified filter with an explicit value-status policy."""

    reference: FieldReference
    operator: FilterOperator
    value: object = MISSING
    status: SavedReferenceStatus = "current"

    def __post_init__(self) -> None:
        if not isinstance(self.reference, FieldReference):
            raise TypeError("reference must be a FieldReference")
        if self.operator not in _FILTER_OPERATORS:
            raise ValueError(f"unsupported specification filter operator: {self.operator!r}")
        if self.status not in _FILTER_STATUSES:
            raise ValueError(f"unsupported specification filter status: {self.status!r}")
        presence_operator = self.operator in {"is_missing", "is_null", "is_empty"}
        if presence_operator and self.value is not MISSING:
            raise ValueError(f"{self.operator} does not accept a value")
        if not presence_operator and self.value is MISSING:
            raise ValueError(f"{self.operator} requires a value")
        _validate_json_filter_value(self.value)

    @property
    def status_state(self) -> ValueStatus:
        return "historical" if self.status == "history" else self.status  # type: ignore[return-value]

    def to_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            **self.reference.to_mapping(),
            "operator": self.operator,
            "status": self.status,
        }
        if self.value is not MISSING:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class SavedReferenceImpact:
    """Read-only inventory result for a saved filter/report reference."""

    path: tuple[str, ...]
    reference: FieldReference | None
    status: Literal["valid", "historical", "unresolved", "legacy"]
    reason: str


def parse_filter_document(
    document: Mapping[str, object] | Sequence[Mapping[str, object]] | str,
) -> tuple[FieldFilter, ...]:
    """Parse the persisted source-qualified filter document.

    A document is either ``{"filters": [...]}`` or the list itself.  The
    parser intentionally rejects label/raw-path forms instead of trying to
    repair or retarget them.
    """

    if isinstance(document, str):
        try:
            document = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ValueError("specification filter document is not valid JSON") from exc
    if isinstance(document, Mapping):
        if set(document) - {"filters"}:
            raise ValueError("specification filter document has unknown properties")
        raw_filters = document.get("filters")
    else:
        raw_filters = document
    if not isinstance(raw_filters, (list, tuple)):
        raise ValueError("specification filter document requires a filters sequence")

    parsed: list[FieldFilter] = []
    for index, raw_filter in enumerate(raw_filters):
        if not isinstance(raw_filter, Mapping):
            raise ValueError(f"filter at index {index} must be an object")
        if set(raw_filter) - {"source", "field_key", "operator", "value", "status"}:
            raise ValueError(f"filter at index {index} has unknown properties")
        reference = FieldReference.from_mapping(raw_filter)
        try:
            operator = raw_filter["operator"]
        except KeyError as exc:
            raise ValueError(f"filter at index {index} requires an operator") from exc
        status = raw_filter.get("status", "current")
        value = raw_filter["value"] if "value" in raw_filter else MISSING
        parsed.append(
            FieldFilter(
                reference=reference,
                operator=operator,  # type: ignore[arg-type]
                value=value,
                status=status,  # type: ignore[arg-type]
            )
        )
    return tuple(parsed)


def identify_saved_references(
    document: object,
    *,
    known_references: Mapping[FieldReference, str] | None = None,
) -> tuple[SavedReferenceImpact, ...]:
    """Inventory references before a saved filter/report is migrated or used.

    ``known_references`` maps canonical references to ``current`` or
    ``historical``.  Legacy objects that expose only a label, form key, or raw
    path are reported as ``legacy`` and are never guessed into a new identity.
    """

    known = known_references or {}
    impacts: list[SavedReferenceImpact] = []

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            looks_like_filter = "operator" in value and (
                "source" in value or "field_key" in value or "field" in value or "label" in value
            )
            if looks_like_filter:
                try:
                    reference = FieldReference.from_mapping(value)
                except (TypeError, ValueError) as exc:
                    impacts.append(SavedReferenceImpact(path, None, "legacy", str(exc)))
                else:
                    saved_status = known.get(reference)
                    if saved_status == "historical":
                        status = "historical"
                    elif saved_status == "current":
                        status = "valid"
                    elif saved_status is None:
                        status = "unresolved"
                    else:
                        status = "legacy"
                    impacts.append(SavedReferenceImpact(path, reference, status, "canonical reference"))
            for key, nested in value.items():
                walk(nested, (*path, str(key)))
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                walk(nested, (*path, str(index)))

    walk(document, ())
    return tuple(impacts)
