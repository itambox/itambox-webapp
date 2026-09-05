"""Pure codecs and set/clear normalization for specification values.

The module deliberately has no Django, request, ORM, or provider dependency. Transport
adapters are responsible for syntax conversion before entering this boundary; the
functions here validate canonical values and return immutable multi-select tuples.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Final, Literal, NoReturn, TypeAlias

from .contracts import FieldDefinitionDTO, FieldKey, JSONValue

SAFE_INTEGER_MIN = -9007199254740991
SAFE_INTEGER_MAX = 9007199254740991
MAX_TEXT_LENGTH = 4096
MAX_DECIMAL_SCALE = 6
MAX_DECIMAL_INTEGRAL_DIGITS = 18
MAX_MULTI_SELECT_VALUES = 64


JSONInputScalar: TypeAlias = str | int | bool | None
JSONInputValue: TypeAlias = (
    JSONInputScalar | list["JSONInputValue"] | tuple["JSONInputValue", ...] | Mapping[str, "JSONInputValue"]
)
FieldSetter: TypeAlias = tuple[str, JSONInputValue]
SpecificationOperation: TypeAlias = Literal[
    "create",
    "asset_type_switch",
    "composition_edit",
    "specification_edit",
    "value_edit",
    "native_edit",
    "checkout",
    "assignment",
    "audit",
    "label_download",
    "historical_cleanup",
]
SpecificationIssueCode: TypeAlias = Literal[
    "INVALID_TYPE",
    "INVALID_DECIMAL",
    "INVALID_RANGE",
    "INVALID_DATE",
    "INVALID_CHOICE",
    "REQUIRED_FIELD",
    "UNKNOWN_FIELD_KEY",
    "READ_ONLY_FIELD",
    "CONFLICT_CLEAR_OVERLAP",
    "DUPLICATE_FIELD",
]


class MissingValue(Enum):
    """Explicit marker for an absent storage or patch value."""

    VALUE = "missing"


MISSING: Final[MissingValue] = MissingValue.VALUE


@dataclass(frozen=True)
class SpecificationCodecIssue:
    """A stable pure-layer issue before an adapter maps its transport path."""

    code: SpecificationIssueCode
    path: tuple[str, ...]
    field_key: FieldKey | None
    message_key: str


@dataclass(frozen=True)
class NormalizedSpecificationPatch:
    """The validated set/clear result and the complete proposed stored map."""

    set_values: Mapping[FieldKey, JSONInputValue]
    clear_keys: tuple[FieldKey, ...]
    stored_values: Mapping[FieldKey, JSONInputValue]


_MESSAGE_KEYS: Mapping[SpecificationIssueCode, str] = {
    "INVALID_TYPE": "specifications.invalid_type",
    "INVALID_DECIMAL": "specifications.invalid_decimal",
    "INVALID_RANGE": "specifications.invalid_range",
    "INVALID_DATE": "specifications.invalid_date",
    "INVALID_CHOICE": "specifications.invalid_choice",
    "REQUIRED_FIELD": "specifications.required_field",
    "UNKNOWN_FIELD_KEY": "specifications.unknown_field_key",
    "READ_ONLY_FIELD": "specifications.read_only_field",
    "CONFLICT_CLEAR_OVERLAP": "specifications.conflict_clear_overlap",
    "DUPLICATE_FIELD": "specifications.duplicate_field",
}


class SpecificationCodecError(ValueError):
    """Raised when a pure codec or patch has one or more stable issues."""

    def __init__(self, issues: Sequence[SpecificationCodecIssue]):
        self.issues = tuple(issues)
        if not self.issues:
            raise ValueError("SpecificationCodecError requires at least one issue")
        first = self.issues[0]
        self.code = first.code
        self.path = first.path
        self.field_key = first.field_key
        super().__init__(first.message_key)


def _issue(
    code: SpecificationIssueCode,
    path: Sequence[str],
    field_key: FieldKey | None = None,
) -> SpecificationCodecIssue:
    return SpecificationCodecIssue(code, tuple(path), field_key, _MESSAGE_KEYS[code])


def _raise_issue(
    code: SpecificationIssueCode,
    path: Sequence[str],
    field_key: FieldKey | None = None,
) -> NoReturn:
    raise SpecificationCodecError((_issue(code, path, field_key),))


def _required_empty_issue(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    path: Sequence[str],
) -> SpecificationCodecIssue | None:
    if not definition.required:
        return None
    if value is None:
        return _issue("REQUIRED_FIELD", path, definition.key)
    if definition.field_type == "text" and type(value) is str and not value.strip():
        return _issue("REQUIRED_FIELD", path, definition.key)
    if definition.field_type in {"date", "decimal", "single_select"} and value == "":
        return _issue("REQUIRED_FIELD", path, definition.key)
    if definition.field_type == "multi_select" and type(value) in {list, tuple} and not value:
        return _issue("REQUIRED_FIELD", path, definition.key)
    return None


def _parse_bound(raw: str | None, path: Sequence[str], field_key: FieldKey) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise SpecificationCodecError((_issue("INVALID_RANGE", path, field_key),)) from exc


def _check_numeric_bounds(
    definition: FieldDefinitionDTO,
    value: Decimal,
    path: Sequence[str],
) -> None:
    minimum = _parse_bound(definition.validation.minimum, path, definition.key)
    maximum = _parse_bound(definition.validation.maximum, path, definition.key)
    if minimum is not None and value < minimum:
        _raise_issue("INVALID_RANGE", path, definition.key)
    if maximum is not None and value > maximum:
        _raise_issue("INVALID_RANGE", path, definition.key)


def _validate_text_characters(
    definition: FieldDefinitionDTO,
    value: str,
    path: Sequence[str],
) -> None:
    if "\x00" in value:
        _raise_issue("INVALID_TYPE", path, definition.key)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _raise_issue("INVALID_TYPE", path, definition.key)


def _validate_text_length(
    definition: FieldDefinitionDTO,
    value: str,
    path: Sequence[str],
) -> None:
    configured_limit = definition.validation.max_length
    limit = MAX_TEXT_LENGTH if configured_limit is None else min(MAX_TEXT_LENGTH, configured_limit)
    if limit < 1 or len(value) > limit:
        _raise_issue("INVALID_RANGE", path, definition.key)


def _validate_text_pattern(
    definition: FieldDefinitionDTO,
    value: str,
    path: Sequence[str],
) -> None:
    pattern = definition.validation.regex
    if pattern is None:
        return
    try:
        compiled = re.compile(pattern, re.ASCII)
    except (re.error, OverflowError, ValueError) as exc:
        raise SpecificationCodecError((_issue("INVALID_RANGE", path, definition.key),)) from exc
    if compiled.fullmatch(value) is None:
        _raise_issue("INVALID_TYPE", path, definition.key)


def _validate_text_rule(
    definition: FieldDefinitionDTO,
    value: str,
    path: Sequence[str],
) -> None:
    if definition.validation.rule != "rfc1123_hostname":
        return
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    hostname = re.compile(rf"{label}(?:\.{label})*", re.ASCII)
    if not 1 <= len(value) <= 253 or hostname.fullmatch(value) is None:
        _raise_issue("INVALID_TYPE", path, definition.key)


def _normalize_text(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    path: Sequence[str],
) -> str:
    if type(value) is not str:
        _raise_issue("INVALID_TYPE", path, definition.key)
    _validate_text_characters(definition, value, path)
    _validate_text_length(definition, value, path)
    if value == "" and not definition.required:
        return value
    _validate_text_pattern(definition, value, path)
    _validate_text_rule(definition, value, path)
    return value


def _normalize_integer(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    path: Sequence[str],
) -> int:
    if type(value) is not int:
        _raise_issue("INVALID_TYPE", path, definition.key)
    if not SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX:
        _raise_issue("INVALID_RANGE", path, definition.key)
    _check_numeric_bounds(definition, Decimal(value), path)
    return value


def _normalize_decimal(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    path: Sequence[str],
) -> str:
    if type(value) is not str:
        _raise_issue("INVALID_TYPE", path, definition.key)

    scale = definition.validation.scale
    if type(scale) is not int or isinstance(scale, bool) or not 0 <= scale <= MAX_DECIMAL_SCALE:
        _raise_issue("INVALID_RANGE", path, definition.key)

    match = re.fullmatch(r"(?P<sign>-?)(?P<integer>0|[1-9][0-9]*)(?:\.(?P<fraction>[0-9]+))?", value)
    if match is None:
        _raise_issue("INVALID_DECIMAL", path, definition.key)
    integer_part = match.group("integer")
    fraction_part = match.group("fraction")
    if len(integer_part) > MAX_DECIMAL_INTEGRAL_DIGITS:
        _raise_issue("INVALID_DECIMAL", path, definition.key)
    if fraction_part is not None and len(fraction_part) > scale:
        _raise_issue("INVALID_DECIMAL", path, definition.key)
    is_negative_zero = (
        match.group("sign") == "-" and integer_part == "0" and (fraction_part is None or set(fraction_part) == {"0"})
    )
    if is_negative_zero:
        _raise_issue("INVALID_DECIMAL", path, definition.key)

    padded_fraction = "" if scale == 0 else (fraction_part or "").ljust(scale, "0")
    canonical = f"{match.group('sign')}{integer_part}"
    if scale:
        canonical += f".{padded_fraction}"
    try:
        decimal_value = Decimal(canonical)
    except InvalidOperation as exc:
        _raise_issue("INVALID_DECIMAL", path, definition.key)
        raise AssertionError("unreachable") from exc
    _check_numeric_bounds(definition, decimal_value, path)
    return canonical


def _normalize_date(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    path: Sequence[str],
) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        _raise_issue("INVALID_DATE", path, definition.key)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SpecificationCodecError((_issue("INVALID_DATE", path, definition.key),)) from exc
    if parsed.isoformat() != value:
        _raise_issue("INVALID_DATE", path, definition.key)
    return value


def _choice_rows(definition: FieldDefinitionDTO) -> Mapping[str, Literal["active", "deprecated"]]:
    choice_set = definition.choice_set
    if choice_set is None or choice_set.lifecycle != "active":
        _raise_issue("INVALID_CHOICE", ("definition", str(definition.key)), definition.key)
    rows: dict[str, Literal["active", "deprecated"]] = {}
    for choice in choice_set.choices:
        rows[choice.key] = choice.lifecycle
    return rows


def _choice_positions(definition: FieldDefinitionDTO) -> Mapping[str, int]:
    choice_set = definition.choice_set
    if choice_set is None or choice_set.lifecycle != "active":
        return {}
    return {choice.key: choice.position for choice in choice_set.choices}


def _original_contains_choice(
    original_value: JSONInputValue | MissingValue,
    field_type: Literal["single_select", "multi_select"],
    key: str,
) -> bool:
    if original_value is MISSING:
        return False
    if field_type == "single_select":
        return type(original_value) is str and original_value == key
    return type(original_value) in {list, tuple} and key in original_value


def _normalize_single_select(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> str:
    if type(value) is not str:
        _raise_issue("INVALID_TYPE", path, definition.key)
    if not value:
        _raise_issue("INVALID_CHOICE", path, definition.key)
    rows = _choice_rows(definition)
    lifecycle = rows.get(value)
    if lifecycle is None or (
        lifecycle == "deprecated" and not _original_contains_choice(original_value, "single_select", value)
    ):
        _raise_issue("INVALID_CHOICE", path, definition.key)
    return value


def _normalize_multi_select(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> tuple[str, ...]:
    if type(value) not in {list, tuple}:
        _raise_issue("INVALID_TYPE", path, definition.key)
    if any(type(item) is not str for item in value):
        _raise_issue("INVALID_TYPE", path, definition.key)
    if len(value) != len(set(value)):
        _raise_issue("INVALID_CHOICE", path, definition.key)

    configured_limit = definition.validation.max_values
    limit = MAX_MULTI_SELECT_VALUES if configured_limit is None else min(MAX_MULTI_SELECT_VALUES, configured_limit)
    if limit < 0 or len(value) > limit:
        _raise_issue("INVALID_RANGE", path, definition.key)

    rows = _choice_rows(definition)
    for key in value:
        lifecycle = rows.get(key)
        if lifecycle is None or (
            lifecycle == "deprecated" and not _original_contains_choice(original_value, "multi_select", key)
        ):
            _raise_issue("INVALID_CHOICE", path, definition.key)
    return tuple(sorted(value))


def _normalize_text_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    del original_value
    return _normalize_text(definition, value, path)


def _normalize_integer_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    del original_value
    return _normalize_integer(definition, value, path)


def _normalize_decimal_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    del original_value
    return _normalize_decimal(definition, value, path)


def _normalize_boolean_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    del original_value
    if type(value) is not bool:
        _raise_issue("INVALID_TYPE", path, definition.key)
    return value


def _normalize_date_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    del original_value
    return _normalize_date(definition, value, path)


ValueNormalizer: TypeAlias = Callable[
    [FieldDefinitionDTO, JSONInputValue, JSONInputValue | MissingValue, Sequence[str]], JSONValue
]
_VALUE_NORMALIZERS: Mapping[str, ValueNormalizer] = {
    "text": _normalize_text_value,
    "integer": _normalize_integer_value,
    "decimal": _normalize_decimal_value,
    "boolean": _normalize_boolean_value,
    "date": _normalize_date_value,
    "single_select": _normalize_single_select,
    "multi_select": _normalize_multi_select,
}


def _normalize_value_unchecked(
    definition: FieldDefinitionDTO,
    value: JSONInputValue,
    original_value: JSONInputValue | MissingValue,
    path: Sequence[str],
) -> JSONValue:
    """Normalize one non-missing value without applying requiredness."""
    if value is None:
        if definition.nullable:
            return None
        _raise_issue("INVALID_TYPE", path, definition.key)
    normalizer = _VALUE_NORMALIZERS.get(definition.field_type)
    if normalizer is None:
        _raise_issue("INVALID_TYPE", path, definition.key)
    return normalizer(definition, value, original_value, path)


def normalize_specification_value(
    definition: FieldDefinitionDTO,
    value: JSONInputValue | MissingValue,
    *,
    original_value: JSONInputValue | MissingValue = MISSING,
    path: Sequence[str] = (),
) -> JSONValue:
    """Validate and canonicalize one value against a pure Field definition."""
    if definition.lifecycle == "deprecated":
        _raise_issue("READ_ONLY_FIELD", path, definition.key)
    if value is MISSING:
        _raise_issue("INVALID_TYPE", path, definition.key)

    required_issue = _required_empty_issue(definition, value, path)
    if required_issue is not None:
        raise SpecificationCodecError((required_issue,))
    return _normalize_value_unchecked(definition, value, original_value, path)


def _required_field_issues(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    stored_values: Mapping[str, JSONInputValue],
) -> tuple[SpecificationCodecIssue, ...]:
    issues: list[SpecificationCodecIssue] = []
    for raw_key, definition in sorted(definitions.items(), key=lambda item: str(item[0])):
        key = FieldKey(str(raw_key))
        if not definition.required or definition.lifecycle != "active":
            continue
        value = stored_values.get(key, MISSING)
        if value is MISSING:
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
            continue
        if value is None:
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
            continue
        if definition.field_type == "text" and type(value) is str and not value.strip():
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
            continue
        if definition.field_type in {"date", "decimal", "single_select"} and value == "":
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
            continue
        if definition.field_type == "multi_select" and type(value) in {list, tuple} and not value:
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
            continue
        try:
            _normalize_value_unchecked(definition, value, value, ("required", str(key)))
        except SpecificationCodecError:
            issues.append(_issue("REQUIRED_FIELD", ("required", str(key)), key))
    return tuple(issues)


def validate_required_fields(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    stored_values: Mapping[str, JSONInputValue],
) -> None:
    """Raise stable REQUIRED_FIELD issues for active required final-state values."""
    issues = _required_field_issues(definitions, stored_values)
    if issues:
        raise SpecificationCodecError(issues)


_FULL_REQUIREDNESS_OPERATIONS = frozenset(
    {"create", "asset_type_switch", "composition_edit", "specification_edit", "value_edit"}
)
_NON_REQUIREDNESS_OPERATIONS = frozenset(
    {"native_edit", "checkout", "assignment", "audit", "label_download", "historical_cleanup"}
)


def operation_requires_requiredness(operation: SpecificationOperation) -> bool:
    """Return whether an operation validates all active required final values."""
    if operation in _FULL_REQUIREDNESS_OPERATIONS:
        return True
    if operation in _NON_REQUIREDNESS_OPERATIONS:
        return False
    raise ValueError(f"Unsupported specification operation: {operation}")


def _setter_items(
    setters: Iterable[FieldSetter] | Mapping[str, JSONInputValue] | None,
) -> tuple[FieldSetter, ...]:
    if setters is None or isinstance(setters, (str, bytes)):
        _raise_issue("INVALID_TYPE", ("set",))
    try:
        if isinstance(setters, Mapping):
            return tuple(setters.items())
        return tuple(setters)
    except (TypeError, ValueError) as exc:
        raise SpecificationCodecError((_issue("INVALID_TYPE", ("set",)),)) from exc


def _normalized_setter_items(
    setters: Iterable[FieldSetter] | Mapping[str, JSONInputValue] | None,
) -> tuple[tuple[str, JSONInputValue], ...]:
    normalized: list[tuple[str, JSONInputValue]] = []
    seen: set[str] = set()
    for item in _setter_items(setters):
        if (
            isinstance(item, (str, bytes))
            or not isinstance(item, Sequence)
            or len(item) != 2
            or type(item[0]) is not str
        ):
            _raise_issue("INVALID_TYPE", ("set",))
        key, value = item
        if key in seen:
            _raise_issue("DUPLICATE_FIELD", ("set",), FieldKey(key))
        seen.add(key)
        normalized.append((key, value))
    return tuple(normalized)


def _normalized_clear_keys(clear_keys: Iterable[str] | None) -> tuple[str, ...]:
    if clear_keys is None or isinstance(clear_keys, (str, bytes)):
        _raise_issue("INVALID_TYPE", ("clear",))
    try:
        items = tuple(clear_keys)
    except (TypeError, ValueError) as exc:
        raise SpecificationCodecError((_issue("INVALID_TYPE", ("clear",)),)) from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for key in items:
        if type(key) is not str:
            _raise_issue("INVALID_TYPE", ("clear",))
        if key in seen:
            _raise_issue("DUPLICATE_FIELD", ("clear",), FieldKey(key))
        seen.add(key)
        normalized.append(key)
    return tuple(normalized)


def _validate_patch_structure(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    setter_items: Sequence[tuple[str, JSONInputValue]],
    clear_keys: Sequence[str],
) -> None:
    setter_keys = {key for key, _ in setter_items}
    clear_key_set = set(clear_keys)
    overlap = setter_keys.intersection(clear_key_set)
    if overlap:
        _raise_issue("CONFLICT_CLEAR_OVERLAP", (), FieldKey(sorted(overlap)[0]))
    for key, _ in setter_items:
        if FieldKey(key) not in definitions:
            _raise_issue("UNKNOWN_FIELD_KEY", ("set", key), FieldKey(key))
    for key in clear_keys:
        if FieldKey(key) not in definitions:
            _raise_issue("UNKNOWN_FIELD_KEY", ("clear", key), FieldKey(key))


def _normalize_set_values(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    stored_values: Mapping[str, JSONInputValue],
    setter_items: Sequence[tuple[str, JSONInputValue]],
) -> tuple[dict[FieldKey, JSONValue], tuple[SpecificationCodecIssue, ...]]:
    normalized: dict[FieldKey, JSONValue] = {}
    issues: list[SpecificationCodecIssue] = []
    for key, value in setter_items:
        field_key = FieldKey(key)
        try:
            normalized[field_key] = normalize_specification_value(
                definitions[field_key],
                value,
                original_value=stored_values.get(field_key, MISSING),
                path=("set", key),
            )
        except SpecificationCodecError as exc:
            issues.extend(exc.issues)
    return normalized, tuple(issues)


def _clear_issues(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    clear_keys: Sequence[str],
) -> tuple[SpecificationCodecIssue, ...]:
    return tuple(
        _issue("READ_ONLY_FIELD", ("clear", key), FieldKey(key))
        for key in clear_keys
        if definitions[FieldKey(key)].lifecycle == "deprecated"
    )


def _proposed_stored_values(
    stored_values: Mapping[str, JSONInputValue],
    normalized_values: Mapping[FieldKey, JSONValue],
    clear_keys: Sequence[str],
) -> dict[FieldKey, JSONInputValue]:
    proposed: dict[FieldKey, JSONInputValue] = dict(stored_values)
    proposed.update(normalized_values)
    for key in clear_keys:
        proposed.pop(FieldKey(key), None)
    return proposed


def normalize_specification_patch(
    definitions: Mapping[FieldKey, FieldDefinitionDTO],
    stored_values: Mapping[str, JSONInputValue],
    *,
    setters: Iterable[FieldSetter] | Mapping[str, JSONInputValue] | None = (),
    clear_keys: Iterable[str] | None = (),
    operation: SpecificationOperation = "specification_edit",
    validate_required: bool | None = None,
) -> NormalizedSpecificationPatch:
    """Validate a set/clear patch, then return its complete proposed stored map.

    Setter pairs are materialized and duplicate-checked before any mapping is built.
    The input storage mapping is copied only after all structural and value checks pass,
    so this function never mutates caller-owned state or partially applies a patch.
    """
    setter_items = _normalized_setter_items(setters)
    clear_items = _normalized_clear_keys(clear_keys)
    _validate_patch_structure(definitions, setter_items, clear_items)
    normalized_values, issues = _normalize_set_values(definitions, stored_values, setter_items)
    issues += _clear_issues(definitions, clear_items)
    if issues:
        raise SpecificationCodecError(issues)

    proposed = _proposed_stored_values(stored_values, normalized_values, clear_items)
    should_validate_required = (
        operation_requires_requiredness(operation) if validate_required is None else validate_required
    )
    if should_validate_required:
        required_issues = _required_field_issues(definitions, proposed)
        if required_issues:
            raise SpecificationCodecError(required_issues)

    return NormalizedSpecificationPatch(
        set_values=normalized_values,
        clear_keys=tuple(FieldKey(key) for key in clear_items),
        stored_values=proposed,
    )


def order_multiselect_for_display(
    definition: FieldDefinitionDTO,
    stored_value: Sequence[str],
) -> tuple[str, ...]:
    """Order stored keys by current Choice presentation position without rewriting them."""
    positions = _choice_positions(definition)
    unknown_position = max(positions.values(), default=-1) + 1
    return tuple(sorted(stored_value, key=lambda key: (positions.get(key, unknown_position), key)))
