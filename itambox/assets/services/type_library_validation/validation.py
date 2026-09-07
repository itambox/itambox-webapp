"""Bounded, side-effect-free validation and normalization for Type Library v1.

The module deliberately stops at a validated normalized document and its JCS
bytes. It does not resolve ORM rows, create catalogue objects, issue preview
records, or apply a release.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, TypeAlias

from extras.canonicalization import canonicalize_release_document

from .errors import LibraryValidationError, PathPart, ValidationIssue, issue
from .limits import ValidationLimits
from .parser import parse_json_document

JSONValue: TypeAlias = Any

_NAMESPACE_RE = re.compile(r"(?=.{1,62}$)^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$")
_QUALIFIED_ID_RE = re.compile(r"(?=.{1,190}$)^[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[a-z0-9][a-z0-9._-]{0,126}$")
_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CHOICE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DECIMAL_BOUND_RE = re.compile(r"^-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,6})?$")
_GTIN_RE = re.compile(r"^(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})$")

_FIELD_TYPES = frozenset({"text", "integer", "decimal", "boolean", "date", "single-select", "multi-select"})
_LIFECYCLES = frozenset({"active", "deprecated"})
_ACTIVATIONS = frozenset({"composed", "global"})
_TARGETS = frozenset({"asset_type", "asset"})
_APPLIES_TO = frozenset({"asset", "accessory", "component"})
_HISTORY_REASONS = frozenset({"removed_composition", "deprecated_definition", "deprecated_choice"})
_RULES = frozenset({"rfc1123_hostname", "temperature_max_gte_min", "voltage_max_gte_min", "runtime_requires_load"})
_QUANTITY_UNITS = {
    "length": frozenset({"U", "m", "cm", "mm", "in", "ft"}),
    "mass": frozenset({"kg", "g", "lb", "oz"}),
    "count": frozenset({None}),
    "digital_information": frozenset({"B", "KiB", "MiB", "GiB", "TiB"}),
    "data_rate": frozenset({"bit/s", "Kbit/s", "Mbit/s", "Gbit/s", "Tbit/s"}),
    "power": frozenset({"W", "kW"}),
    "voltage": frozenset({"V", "mV", "kV"}),
    "energy": frozenset({"Wh", "kWh"}),
    "duration": frozenset({"s", "min", "h", "d"}),
    "apparent_power": frozenset({"VA"}),
    "resolution": frozenset({"MP"}),
    "rate": frozenset({"pages_per_minute"}),
    "temperature": frozenset({"°C", "°F", "K"}),
    "sound_pressure": frozenset({"dBA"}),
}

_EXTERNAL = object()


@dataclass(frozen=True, slots=True)
class DependencyReference:
    """Exact dependency identity requested by a release."""

    namespace: str
    release: int
    digest: str


@dataclass(frozen=True, slots=True)
class InstalledDependency:
    """A server-provided dependency anchor; library validation never fetches it."""

    namespace: str
    release: int
    digest: str
    document: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ValidatedLibraryDocument:
    """Validated normalized source plus the bytes that are safe to hash/store."""

    kind: str
    normalized_document: dict[str, Any]
    canonical_bytes: bytes
    semantic_digest: str

    @property
    def digest(self) -> str:
        """Compatibility spelling for callers that call the result a digest."""

        return self.semantic_digest


@dataclass(slots=True)
class _Graph:
    namespace: str
    fields_by_identity: dict[str, dict[str, Any]]
    fields_by_key: dict[str, dict[str, Any]]
    choice_sets: dict[str, dict[str, Any]]
    fieldsets: dict[str, dict[str, Any]]
    categories: dict[str, dict[str, Any]]
    manufacturers: dict[str, dict[str, Any]]
    asset_types: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _ValidatedPart:
    namespace: str
    release: int
    graph: _Graph
    declared_dependencies: frozenset[str]


class _ValidationContext:
    def __init__(self, limits: ValidationLimits, installed_dependencies: Iterable[InstalledDependency]):
        self.limits = limits
        self.installed_dependencies = {}
        for dependency in installed_dependencies:
            identity = (dependency.namespace, dependency.release)
            if identity in self.installed_dependencies:
                raise issue(
                    "DUPLICATE_DEPENDENCY",
                    (),
                    f"Installed dependency {dependency.namespace}/{dependency.release} is repeated",
                )
            self.installed_dependencies[identity] = dependency
        self.counts = {
            "fields": 0,
            "fieldsets": 0,
            "choice_sets": 0,
            "choices": 0,
            "asset_types": 0,
            "dependencies": 0,
        }

    def count(self, name: str, amount: int, path: tuple[PathPart, ...]) -> None:
        self.counts[name] += amount
        limit_name = {
            "fields": "max_fields",
            "fieldsets": "max_fieldsets",
            "choice_sets": "max_choice_sets",
            "choices": "max_total_choices",
            "asset_types": "max_asset_types",
            "dependencies": "max_dependencies",
        }[name]
        limit = getattr(self.limits, limit_name)
        if self.counts[name] > limit:
            raise issue("RESOURCE_LIMIT", path, f"The aggregate {name} count exceeds the limit of {limit}")


def _coerce_installed_dependencies(
    installed_dependencies: Mapping[Any, Any] | Iterable[InstalledDependency] | None,
) -> tuple[InstalledDependency, ...]:
    if installed_dependencies is None:
        return ()
    if isinstance(installed_dependencies, Mapping):
        result: list[InstalledDependency] = []
        for key, value in installed_dependencies.items():
            if isinstance(key, tuple) and len(key) == 2:
                namespace, release = key
            elif isinstance(key, str) and isinstance(value, Mapping):
                namespace = key
                release = value.get("release")
            else:
                raise TypeError("Dependency mappings must use (namespace, release) keys or namespace-to-record values")
            if isinstance(value, InstalledDependency):
                result.append(value)
                continue
            if isinstance(value, str):
                digest = value
                document = None
            elif isinstance(value, Mapping):
                digest = value.get("digest")
                document = value.get("document")
                release = value.get("release", release)
                namespace = value.get("namespace", namespace)
            else:
                raise TypeError("Dependency values must be a digest or a dependency record")
            if type(namespace) is not str or type(release) is not int or type(digest) is not str:
                raise TypeError("Dependency records require namespace, release, and digest")
            result.append(InstalledDependency(namespace, release, digest, document))
        return tuple(result)
    return tuple(installed_dependencies)


def _fail(code: str, path: tuple[PathPart, ...], message: str) -> None:
    raise issue(code, path, message)


def _expect_object(value: Any, path: tuple[PathPart, ...]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("SCHEMA_TYPE", path, "Expected a JSON object")
    return value


def _expect_array(value: Any, path: tuple[PathPart, ...]) -> list[Any]:
    if type(value) is not list:
        _fail("SCHEMA_TYPE", path, "Expected a JSON array")
    return value


def _expect_string(value: Any, path: tuple[PathPart, ...], *, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        _fail("SCHEMA_TYPE", path, "Expected a non-empty string" if nonempty else "Expected a string")
    return value


def _expect_bool(value: Any, path: tuple[PathPart, ...]) -> bool:
    if type(value) is not bool:
        _fail("SCHEMA_TYPE", path, "Expected a boolean")
    return value


def _expect_int(
    value: Any, path: tuple[PathPart, ...], *, minimum: int | None = None, maximum: int | None = None
) -> int:
    if type(value) is not int:
        _fail("SCHEMA_TYPE", path, "Expected an integer")
    if minimum is not None and value < minimum:
        _fail("INVALID_RANGE", path, f"Integer must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail("INVALID_RANGE", path, f"Integer must be at most {maximum}")
    return value


def _expect_enum_array(
    value: Any,
    path: tuple[PathPart, ...],
    *,
    allowed: frozenset[str],
    minimum: int,
    maximum: int,
    label: str,
) -> list[str]:
    items = _expect_array(value, path)
    if not minimum <= len(items) <= maximum:
        _fail("INVALID_RANGE", path, f"{label} must contain between {minimum} and {maximum} entries")
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_path = path + (index,)
        if type(item) is not str:
            _fail("SCHEMA_TYPE", item_path, f"{label} entries must be strings")
        if item not in allowed:
            _fail("INVALID_VALIDATION", item_path, f"Unknown {label} entry")
        if item in seen:
            _fail("DUPLICATE_IDENTITY", item_path, f"{label} entries must be unique")
        seen.add(item)
    return items


def _check_properties(
    value: Any,
    path: tuple[PathPart, ...],
    *,
    required: Iterable[str],
    optional: Iterable[str],
) -> dict[str, Any]:
    obj = _expect_object(value, path)
    allowed = set(required) | set(optional)
    unknown = sorted(set(obj) - allowed)
    if unknown:
        _fail("UNKNOWN_PROPERTY", path + (unknown[0],), f"Property {unknown[0]!r} is not allowed here")
    missing = sorted(set(required) - set(obj))
    if missing:
        _fail("MISSING_PROPERTY", path + (missing[0],), f"Required property {missing[0]!r} is missing")
    return obj


def _check_pattern(value: Any, pattern: re.Pattern[str], path: tuple[PathPart, ...], message: str) -> str:
    value = _expect_string(value, path)
    if pattern.fullmatch(value) is None:
        _fail("INVALID_IDENTITY", path, message)
    return value


def _qualified_identity(value: Any, path: tuple[PathPart, ...]) -> str:
    return _check_pattern(value, _QUALIFIED_ID_RE, path, "Expected a qualified namespace/local identity")


def _owned_identity(value: Any, path: tuple[PathPart, ...], namespace: str | frozenset[str] | None) -> str:
    identity = _qualified_identity(value, path)
    owner = identity.partition("/")[0]
    allowed = {namespace} if isinstance(namespace, str) else namespace
    if allowed is not None and owner not in allowed:
        _fail("NAMESPACE_TAKEOVER", path, f"Identity must remain in publisher namespace {namespace!r}")
    if owner == "catalog":
        _fail("NAMESPACE_TAKEOVER", path, "Publisher definitions may not use the catalog namespace")
    return identity


def _catalog_identity(value: Any, path: tuple[PathPart, ...]) -> str:
    identity = _qualified_identity(value, path)
    if identity.partition("/")[0] != "catalog":
        _fail("NAMESPACE_TAKEOVER", path, "Shared catalogue references must use the catalog namespace")
    return identity


def _validate_safe_regex(pattern: str, path: tuple[PathPart, ...]) -> None:
    if len(pattern) > 256:
        _fail("INVALID_VALIDATION", path, "Regular expressions may not exceed 256 characters")
    if re.search(r"\(\?[aiLmsux-]+(?::|\))", pattern):
        _fail("INVALID_VALIDATION", path, "Inline regular-expression flags are not allowed")
    if re.search(r"\\(?:[1-9]|g<|k<)|\(\?P=", pattern):
        _fail("INVALID_VALIDATION", path, "Regular-expression backreferences are not allowed")
    try:
        re.compile(pattern, re.ASCII)
    except (re.error, OverflowError, ValueError) as exc:
        _fail("INVALID_VALIDATION", path, "Regular expression is not valid")
        raise AssertionError from exc
    # Keep the same bounded policy as generic CustomFieldData validation without
    # importing Django or accepting an executable validator from the document.
    if pattern.count("*") + pattern.count("+") > 1 and "[" not in pattern:
        _fail("INVALID_VALIDATION", path, "Regular expression has too many unbounded repetitions")


def _decimal(value: str, path: tuple[PathPart, ...]) -> Decimal:
    if _DECIMAL_BOUND_RE.fullmatch(value) is None:
        _fail("INVALID_VALIDATION", path, "Expected a bounded base-10 decimal string")
    if value in {"-0", "-0.0", "-0.00", "-0.000", "-0.0000", "-0.00000", "-0.000000"}:
        _fail("INVALID_VALIDATION", path, "Negative zero is not allowed")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        _fail("INVALID_VALIDATION", path, "Decimal metadata is invalid")
        raise AssertionError from exc


def _validate_multi_value_shape(value: list[Any], path: tuple[PathPart, ...]) -> None:
    if len(value) > 64:
        _fail("RESOURCE_LIMIT", path, "Multi-choice values may not contain more than 64 entries")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if type(item) is not str:
            _fail("SCHEMA_TYPE", path + (index,), "Multi-choice values must contain strings")
        _check_pattern(item, _CHOICE_KEY_RE, path + (index,), "Invalid Choice key")
        if item in seen:
            _fail("DUPLICATE_VALUE", path + (index,), "Multi-choice values must be unique")
        seen.add(item)


def _check_generic_value(value: Any, path: tuple[PathPart, ...], limits: ValidationLimits) -> None:
    if type(value) is str:
        if len(value) > 4096:
            _fail("RESOURCE_LIMIT", path, "String values may not exceed 4096 code points")
        return
    if type(value) is int:
        if not -9007199254740991 <= value <= 9007199254740991:
            _fail("INVALID_RANGE", path, "Integer values must remain within the JCS safe-integer range")
        return
    if type(value) is bool or value is None:
        return
    if type(value) is list:
        _validate_multi_value_shape(value, path)
        return
    _fail("UNSUPPORTED_STRUCTURE", path, "Nested specification objects and repeated structures are not supported")


def _validate_field_identity(
    field: dict[str, Any], path: tuple[PathPart, ...], namespace: str | frozenset[str] | None
) -> None:
    key = _check_pattern(field["key"], _FIELD_KEY_RE, path + ("key",), "Invalid Field storage key")
    field_namespace = _check_pattern(
        field["namespace"], _NAMESPACE_RE, path + ("namespace",), "Invalid Field namespace"
    )
    allowed = {namespace} if isinstance(namespace, str) else namespace
    if allowed is not None and field_namespace not in allowed:
        _fail("NAMESPACE_TAKEOVER", path + ("namespace",), f"Field namespace must be {namespace!r}")
    if field_namespace == "catalog":
        _fail("NAMESPACE_TAKEOVER", path + ("namespace",), "Publisher Fields may not use the catalog namespace")
    if field_namespace != "itambox":
        expected_prefix = f"{field_namespace.replace('-', '_')}__"
        if not key.startswith(expected_prefix) or len(key) == len(expected_prefix):
            _fail("INVALID_IDENTITY", path + ("key",), f"Non-core Field keys must start with {expected_prefix!r}")


def _validate_field_surface(field: dict[str, Any], path: tuple[PathPart, ...]) -> str:
    _expect_string(field["label"], path + ("label",), nonempty=True)
    if len(field["label"]) > 200:
        _fail("INVALID_RANGE", path + ("label",), "Labels may not exceed 200 characters")
    _expect_string(field["help_text"], path + ("help_text",))
    if len(field["help_text"]) > 4096:
        _fail("INVALID_RANGE", path + ("help_text",), "Help text may not exceed 4096 characters")
    _expect_enum_array(
        field["targets"],
        path + ("targets",),
        allowed=_TARGETS,
        minimum=1,
        maximum=2,
        label="Targets",
    )
    activation = _expect_string(field["activation"], path + ("activation",))
    if activation not in _ACTIVATIONS:
        _fail("INVALID_VALIDATION", path + ("activation",), "Unknown Field activation")
    field_type = _expect_string(field["field_type"], path + ("field_type",))
    if field_type not in _FIELD_TYPES:
        _fail("INVALID_VALIDATION", path + ("field_type",), "Unknown Field type")
    _expect_bool(field["required"], path + ("required",))
    _expect_bool(field["nullable"], path + ("nullable",))
    lifecycle = _expect_string(field["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Field lifecycle")
    return field_type


def _validate_field_quantities(field: dict[str, Any], path: tuple[PathPart, ...], field_type: str) -> None:
    has_quantity = "quantity_kind" in field
    has_unit = "canonical_unit" in field
    if has_quantity != has_unit:
        _fail("INVALID_VALIDATION", path, "quantity_kind and canonical_unit must be supplied together")
    if not has_quantity:
        return
    if field_type not in {"integer", "decimal"}:
        _fail("INVALID_VALIDATION", path, "Only numeric Fields may declare quantity metadata")
    quantity_kind = _expect_string(field["quantity_kind"], path + ("quantity_kind",))
    unit = field["canonical_unit"]
    if type(unit) is not str and unit is not None:
        _fail("SCHEMA_TYPE", path + ("canonical_unit",), "canonical_unit must be a string or null")
    if len(quantity_kind) > 32 or (isinstance(unit, str) and len(unit) > 16):
        _fail("INVALID_RANGE", path, "Quantity metadata exceeds its bound")
    if quantity_kind not in _QUANTITY_UNITS or unit not in _QUANTITY_UNITS[quantity_kind]:
        _fail("INVALID_VALIDATION", path, "canonical_unit is not valid for quantity_kind")


def _validate_field_choice(field: dict[str, Any], path: tuple[PathPart, ...], field_type: str) -> None:
    if field_type in {"single-select", "multi-select"}:
        if "choice_set" not in field:
            _fail("MISSING_PROPERTY", path + ("choice_set",), "Select Fields require a Choice Set")
        _qualified_identity(field["choice_set"], path + ("choice_set",))
    elif "choice_set" in field:
        _fail("INVALID_VALIDATION", path + ("choice_set",), "Only select Fields may declare a Choice Set")
    if "replaced_by" in field:
        _qualified_identity(field["replaced_by"], path + ("replaced_by",))


def _validate_field(value: Any, path: tuple[PathPart, ...], namespace: str | frozenset[str] | None) -> dict[str, Any]:
    field = _check_properties(
        value,
        path,
        required=(
            "key",
            "namespace",
            "label",
            "help_text",
            "targets",
            "activation",
            "field_type",
            "required",
            "nullable",
            "lifecycle",
            "validation",
        ),
        optional=("quantity_kind", "canonical_unit", "choice_set", "replaced_by"),
    )
    _validate_field_identity(field, path, namespace)
    field_type = _validate_field_surface(field, path)
    validation = _check_properties(
        field["validation"],
        path + ("validation",),
        required=(),
        optional=("max_length", "regex", "rule", "minimum", "maximum", "scale", "max_values"),
    )
    _validate_field_metadata(field, validation, path, field_type)
    _validate_field_quantities(field, path, field_type)
    _validate_field_choice(field, path, field_type)
    return field


def _validate_field_metadata(  # noqa: C901 - type-specific schema branches are one bounded pass
    field: dict[str, Any], validation: dict[str, Any], path: tuple[PathPart, ...], field_type: str
) -> None:
    if field_type == "text":
        if "max_length" not in validation:
            _fail("MISSING_PROPERTY", path + ("validation", "max_length"), "Text Fields require max_length")
        _expect_int(validation["max_length"], path + ("validation", "max_length"), minimum=1, maximum=4096)
        if "regex" in validation:
            _validate_safe_regex(
                _expect_string(validation["regex"], path + ("validation", "regex")), path + ("validation", "regex")
            )
        if "rule" in validation:
            rule = _expect_string(validation["rule"], path + ("validation", "rule"))
            if rule != "rfc1123_hostname":
                _fail("INVALID_VALIDATION", path + ("validation", "rule"), "Unknown text validation rule")
        for key in set(validation) - {"max_length", "regex", "rule"}:
            _fail("INVALID_VALIDATION", path + ("validation", key), "Property is not valid for text Fields")
        return
    if field_type == "integer":
        allowed = {"minimum", "maximum"}
        if set(validation) - allowed:
            _fail("INVALID_VALIDATION", path + ("validation",), "Property is not valid for integer Fields")
        _validate_bounds(validation, path)
        return
    if field_type == "decimal":
        if "scale" not in validation:
            _fail("MISSING_PROPERTY", path + ("validation", "scale"), "Decimal Fields require scale")
        _expect_int(validation["scale"], path + ("validation", "scale"), minimum=0, maximum=6)
        allowed = {"minimum", "maximum", "scale", "rule"}
        if set(validation) - allowed:
            _fail("INVALID_VALIDATION", path + ("validation",), "Property is not valid for decimal Fields")
        _validate_bounds(validation, path)
        if "rule" in validation:
            rule = _expect_string(validation["rule"], path + ("validation", "rule"))
            if rule not in _RULES - {"rfc1123_hostname"}:
                _fail("INVALID_VALIDATION", path + ("validation", "rule"), "Unknown decimal validation rule")
        return
    if field_type in {"boolean", "date"}:
        if validation:
            _fail(
                "INVALID_VALIDATION", path + ("validation",), f"{field_type} Fields do not accept validation metadata"
            )
        return
    if field_type == "single-select":
        if set(validation) != {"max_values"} or validation.get("max_values") != 1:
            _fail("INVALID_VALIDATION", path + ("validation",), "Single-select Fields require max_values=1")
        return
    if field_type == "multi-select":
        if set(validation) != {"max_values"}:
            _fail("INVALID_VALIDATION", path + ("validation",), "Multi-select Fields require max_values")
        _expect_int(validation["max_values"], path + ("validation", "max_values"), minimum=1, maximum=64)


def _validate_bounds(validation: dict[str, Any], path: tuple[PathPart, ...]) -> None:
    parsed: dict[str, Decimal] = {}
    for name in ("minimum", "maximum"):
        if name in validation:
            raw = _expect_string(validation[name], path + ("validation", name))
            parsed[name] = _decimal(raw, path + ("validation", name))
    if "minimum" in parsed and "maximum" in parsed and parsed["minimum"] > parsed["maximum"]:
        _fail("INVALID_RANGE", path + ("validation",), "minimum may not exceed maximum")


def _validate_choice(value: Any, path: tuple[PathPart, ...]) -> dict[str, Any]:
    choice = _check_properties(
        value,
        path,
        required=("key", "label", "lifecycle"),
        optional=("replaced_by",),
    )
    _check_pattern(choice["key"], _CHOICE_KEY_RE, path + ("key",), "Invalid Choice key")
    _expect_string(choice["label"], path + ("label",), nonempty=True)
    if len(choice["label"]) > 200:
        _fail("INVALID_RANGE", path + ("label",), "Choice labels may not exceed 200 characters")
    lifecycle = _expect_string(choice["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Choice lifecycle")
    if "replaced_by" in choice:
        _check_pattern(choice["replaced_by"], _CHOICE_KEY_RE, path + ("replaced_by",), "Invalid replacement Choice key")
    return choice


def _validate_choice_set(
    value: Any,
    path: tuple[PathPart, ...],
    namespace: str | frozenset[str] | None,
    ctx: _ValidationContext,
) -> dict[str, Any]:
    choice_set = _check_properties(
        value,
        path,
        required=("id", "label", "description", "lifecycle", "choices"),
        optional=("replaced_by",),
    )
    identity = _owned_identity(choice_set["id"], path + ("id",), namespace)
    _expect_string(choice_set["label"], path + ("label",), nonempty=True)
    _expect_string(choice_set["description"], path + ("description",))
    if len(choice_set["description"]) > 4096:
        _fail("INVALID_RANGE", path + ("description",), "Descriptions may not exceed 4096 characters")
    lifecycle = _expect_string(choice_set["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Choice Set lifecycle")
    choices = _expect_array(choice_set["choices"], path + ("choices",))
    if len(choices) > ctx.limits.max_choices_per_set:
        _fail("RESOURCE_LIMIT", path + ("choices",), "Choice Set contains too many Choices")
    ctx.count("choices", len(choices), path + ("choices",))
    seen: set[str] = set()
    for index, choice_value in enumerate(choices):
        choice = _validate_choice(choice_value, path + ("choices", index))
        if choice["key"] in seen:
            _fail(
                "DUPLICATE_IDENTITY", path + ("choices", index, "key"), "Choice keys must be unique within a Choice Set"
            )
        seen.add(choice["key"])
    if "replaced_by" in choice_set:
        _qualified_identity(choice_set["replaced_by"], path + ("replaced_by",))
    choice_set["id"] = identity
    return choice_set


def _validate_fieldset(
    value: Any,
    path: tuple[PathPart, ...],
    namespace: str | frozenset[str] | None,
    ctx: _ValidationContext,
) -> dict[str, Any]:
    fieldset = _check_properties(
        value,
        path,
        required=("id", "label", "description", "lifecycle", "fields"),
        optional=("replaced_by",),
    )
    identity = _owned_identity(fieldset["id"], path + ("id",), namespace)
    _expect_string(fieldset["label"], path + ("label",), nonempty=True)
    _expect_string(fieldset["description"], path + ("description",))
    if len(fieldset["description"]) > 4096:
        _fail("INVALID_RANGE", path + ("description",), "Descriptions may not exceed 4096 characters")
    lifecycle = _expect_string(fieldset["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Fieldset lifecycle")
    fields = _expect_array(fieldset["fields"], path + ("fields",))
    if len(fields) > ctx.limits.max_fields_per_section:
        _fail("RESOURCE_LIMIT", path + ("fields",), "Fieldset contains too many Fields")
    seen: set[str] = set()
    for index, field_ref in enumerate(fields):
        ref = _qualified_identity(field_ref, path + ("fields", index))
        if ref in seen:
            _fail("DUPLICATE_IDENTITY", path + ("fields", index), "A Field may occur only once in one Fieldset")
        seen.add(ref)
    if "replaced_by" in fieldset:
        _qualified_identity(fieldset["replaced_by"], path + ("replaced_by",))
    fieldset["id"] = identity
    return fieldset


def _validate_category(value: Any, path: tuple[PathPart, ...]) -> dict[str, Any]:
    category = _check_properties(
        value,
        path,
        required=("id", "label", "description", "lifecycle", "applies_to", "default_fieldsets"),
        optional=(),
    )
    identity = _catalog_identity(category["id"], path + ("id",))
    _expect_string(category["label"], path + ("label",), nonempty=True)
    _expect_string(category["description"], path + ("description",))
    lifecycle = _expect_string(category["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Category lifecycle")
    _expect_enum_array(
        category["applies_to"],
        path + ("applies_to",),
        allowed=_APPLIES_TO,
        minimum=1,
        maximum=3,
        label="applies_to",
    )
    defaults = _expect_array(category["default_fieldsets"], path + ("default_fieldsets",))
    if len(defaults) > 32:
        _fail("RESOURCE_LIMIT", path + ("default_fieldsets",), "Category has too many default Fieldsets")
    normalized_defaults = [
        _qualified_identity(fieldset_ref, path + ("default_fieldsets", index))
        for index, fieldset_ref in enumerate(defaults)
    ]
    if len(set(normalized_defaults)) != len(normalized_defaults):
        _fail("DUPLICATE_IDENTITY", path + ("default_fieldsets",), "Category default Fieldsets must be unique")
    category["default_fieldsets"] = normalized_defaults
    category["id"] = identity
    return category


def _validate_manufacturer(value: Any, path: tuple[PathPart, ...]) -> dict[str, Any]:
    manufacturer = _check_properties(
        value,
        path,
        required=("id", "label", "description", "lifecycle"),
        optional=(),
    )
    identity = _catalog_identity(manufacturer["id"], path + ("id",))
    _expect_string(manufacturer["label"], path + ("label",), nonempty=True)
    _expect_string(manufacturer["description"], path + ("description",))
    lifecycle = _expect_string(manufacturer["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Manufacturer lifecycle")
    manufacturer["id"] = identity
    return manufacturer


def _validate_specification_map(value: Any, path: tuple[PathPart, ...], limits: ValidationLimits) -> dict[str, Any]:
    specifications = _expect_object(value, path)
    if len(specifications) > limits.max_specifications_per_type:
        _fail("RESOURCE_LIMIT", path, "Asset Type has too many specification entries")
    for key, item in specifications.items():
        _check_pattern(key, _FIELD_KEY_RE, path + (key,), "Invalid specification Field key")
        _check_generic_value(item, path + (key,), limits)
    return specifications


def _validate_historical_map(value: Any, path: tuple[PathPart, ...], limits: ValidationLimits) -> dict[str, Any]:
    history = _expect_object(value, path)
    if len(history) > limits.max_historical_specifications_per_type:
        _fail("RESOURCE_LIMIT", path, "Asset Type has too many historical specification entries")
    for key, item in history.items():
        _check_pattern(key, _FIELD_KEY_RE, path + (key,), "Invalid historical Field key")
        record = _check_properties(item, path + (key,), required=("value", "reason"), optional=())
        _check_generic_value(record["value"], path + (key, "value"), limits)
        reason = _expect_string(record["reason"], path + (key, "reason"))
        if reason not in _HISTORY_REASONS:
            _fail("INVALID_VALIDATION", path + (key, "reason"), "Unknown historical value reason")
    return history


def _validate_asset_type(  # noqa: C901 - one bounded structural pass keeps all Type limits together
    value: Any,
    path: tuple[PathPart, ...],
    namespace: str | frozenset[str] | None,
    ctx: _ValidationContext,
) -> dict[str, Any]:
    asset_type = _check_properties(
        value,
        path,
        required=(
            "id",
            "manufacturer",
            "model",
            "part_number",
            "gtin",
            "region",
            "configuration",
            "category",
            "description",
            "lifecycle",
            "fieldsets",
            "specifications",
            "historical_specifications",
        ),
        optional=("replaced_by",),
    )
    identity = _owned_identity(asset_type["id"], path + ("id",), namespace)
    _catalog_identity(asset_type["manufacturer"], path + ("manufacturer",))
    _expect_string(asset_type["model"], path + ("model",), nonempty=True)
    if len(asset_type["model"]) > 255:
        _fail("INVALID_RANGE", path + ("model",), "Models may not exceed 255 characters")
    for name, maximum in (("part_number", 100), ("region", 64), ("configuration", 255), ("description", 4096)):
        _expect_string(asset_type[name], path + (name,))
        if len(asset_type[name]) > maximum:
            _fail("INVALID_RANGE", path + (name,), f"{name} exceeds its length bound")
    gtin = asset_type["gtin"]
    if gtin is not None:
        gtin = _expect_string(gtin, path + ("gtin",))
        if _GTIN_RE.fullmatch(gtin) is None:
            _fail("INVALID_VALIDATION", path + ("gtin",), "GTIN must contain 8, 12, 13, or 14 digits")
    category = asset_type["category"]
    if category is not None:
        _catalog_identity(category, path + ("category",))
    lifecycle = _expect_string(asset_type["lifecycle"], path + ("lifecycle",))
    if lifecycle not in _LIFECYCLES:
        _fail("INVALID_VALIDATION", path + ("lifecycle",), "Unknown Asset Type lifecycle")
    fieldsets = _expect_array(asset_type["fieldsets"], path + ("fieldsets",))
    if len(fieldsets) > ctx.limits.max_sections_per_type:
        _fail("RESOURCE_LIMIT", path + ("fieldsets",), "Asset Type has too many Fieldsets")
    normalized_fieldsets = [
        _qualified_identity(fieldset_ref, path + ("fieldsets", index)) for index, fieldset_ref in enumerate(fieldsets)
    ]
    if len(set(normalized_fieldsets)) != len(normalized_fieldsets):
        _fail("DUPLICATE_IDENTITY", path + ("fieldsets",), "Asset Type Fieldsets must be unique")
    asset_type["fieldsets"] = normalized_fieldsets
    _validate_specification_map(asset_type["specifications"], path + ("specifications",), ctx.limits)
    _validate_historical_map(asset_type["historical_specifications"], path + ("historical_specifications",), ctx.limits)
    if "replaced_by" in asset_type:
        _qualified_identity(asset_type["replaced_by"], path + ("replaced_by",))
    asset_type["id"] = identity
    return asset_type


def _register(mapping: dict[str, dict[str, Any]], key: str, value: dict[str, Any], path: tuple[PathPart, ...]) -> None:
    if key in mapping:
        _fail("DUPLICATE_IDENTITY", path, f"Identity {key!r} is repeated")
    mapping[key] = value


def _validate_definitions(
    definitions_value: Any,
    path: tuple[PathPart, ...],
    namespace: str,
    ctx: _ValidationContext,
    declared_dependencies: frozenset[str],
    *,
    allow_additional_namespaces: bool = False,
) -> _Graph:
    definitions = _check_properties(
        definitions_value,
        path,
        required=("choice_sets", "fields", "fieldsets", "categories", "manufacturers", "asset_types"),
        optional=(),
    )
    choice_sets = _expect_array(definitions["choice_sets"], path + ("choice_sets",))
    fields = _expect_array(definitions["fields"], path + ("fields",))
    fieldsets = _expect_array(definitions["fieldsets"], path + ("fieldsets",))
    categories = _expect_array(definitions["categories"], path + ("categories",))
    manufacturers = _expect_array(definitions["manufacturers"], path + ("manufacturers",))
    asset_types = _expect_array(definitions["asset_types"], path + ("asset_types",))
    for name, items, maximum in (
        ("choice_sets", choice_sets, ctx.limits.max_choice_sets),
        ("fields", fields, ctx.limits.max_fields),
        ("fieldsets", fieldsets, ctx.limits.max_fieldsets),
        ("asset_types", asset_types, ctx.limits.max_asset_types),
        ("categories", categories, 256),
        ("manufacturers", manufacturers, 256),
    ):
        if len(items) > maximum:
            _fail("RESOURCE_LIMIT", path + (name,), f"Too many {name}")
    ctx.count("choice_sets", len(choice_sets), path + ("choice_sets",))
    ctx.count("fields", len(fields), path + ("fields",))
    ctx.count("fieldsets", len(fieldsets), path + ("fieldsets",))
    ctx.count("asset_types", len(asset_types), path + ("asset_types",))
    owner_namespace: str | None = None if allow_additional_namespaces else namespace
    graph = _Graph(namespace, {}, {}, {}, {}, {}, {}, {})
    for index, value in enumerate(choice_sets):
        choice_set = _validate_choice_set(value, path + ("choice_sets", index), owner_namespace, ctx)
        _register(graph.choice_sets, choice_set["id"], choice_set, path + ("choice_sets", index, "id"))
    for index, value in enumerate(fields):
        field = _validate_field(value, path + ("fields", index), owner_namespace)
        identity = f"{field['namespace']}/{field['key']}"
        _register(graph.fields_by_identity, identity, field, path + ("fields", index, "key"))
        if field["key"] in graph.fields_by_key:
            _fail("DUPLICATE_IDENTITY", path + ("fields", index, "key"), "Field storage keys are globally unique")
        graph.fields_by_key[field["key"]] = field
    for index, value in enumerate(fieldsets):
        fieldset = _validate_fieldset(value, path + ("fieldsets", index), owner_namespace, ctx)
        _register(graph.fieldsets, fieldset["id"], fieldset, path + ("fieldsets", index, "id"))
    for index, value in enumerate(categories):
        category = _validate_category(value, path + ("categories", index))
        _register(graph.categories, category["id"], category, path + ("categories", index, "id"))
    for index, value in enumerate(manufacturers):
        manufacturer = _validate_manufacturer(value, path + ("manufacturers", index))
        _register(graph.manufacturers, manufacturer["id"], manufacturer, path + ("manufacturers", index, "id"))
    for index, value in enumerate(asset_types):
        asset_type = _validate_asset_type(value, path + ("asset_types", index), owner_namespace, ctx)
        _register(graph.asset_types, asset_type["id"], asset_type, path + ("asset_types", index, "id"))
    _validate_graph(graph, path, ctx, declared_dependencies)
    return graph


def _resolve(
    mapping: dict[str, dict[str, Any]],
    identity: str,
    path: tuple[PathPart, ...],
    declared_dependencies: frozenset[str],
) -> dict[str, Any] | object:
    if identity in mapping:
        return mapping[identity]
    namespace = identity.partition("/")[0]
    if namespace in declared_dependencies:
        return _EXTERNAL
    _fail("INVALID_REFERENCE", path, f"Reference {identity!r} does not resolve")


def _resolve_catalog(
    mapping: dict[str, dict[str, Any]], identity: str, path: tuple[PathPart, ...]
) -> dict[str, Any] | object:
    if identity in mapping:
        return mapping[identity]
    if identity.partition("/")[0] == "catalog":
        return _EXTERNAL
    _fail("INVALID_REFERENCE", path, f"Catalogue reference {identity!r} does not resolve")


def _validate_replacements(  # noqa: C901 - all same-kind replacement edges share one cycle walk
    graph: _Graph, path: tuple[PathPart, ...], declared: frozenset[str]
) -> None:
    edges: dict[tuple[str, str], tuple[str, str]] = {}
    for identity, field in graph.fields_by_identity.items():
        if "replaced_by" in field:
            target = _resolve(
                graph.fields_by_identity, field["replaced_by"], path + ("fields", identity, "replaced_by"), declared
            )
            if target is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("fields", identity, "replaced_by"),
                    "Replacement dependency definitions are not loaded",
                )
            edges[("field", identity)] = ("field", field["replaced_by"])
    for identity, choice_set in graph.choice_sets.items():
        if "replaced_by" in choice_set:
            target = _resolve(
                graph.choice_sets, choice_set["replaced_by"], path + ("choice_sets", identity, "replaced_by"), declared
            )
            if target is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("choice_sets", identity, "replaced_by"),
                    "Replacement dependency definitions are not loaded",
                )
            edges[("choice_set", identity)] = ("choice_set", choice_set["replaced_by"])
        choices = {choice["key"]: choice for choice in choice_set["choices"]}
        for key, choice in choices.items():
            if "replaced_by" not in choice:
                continue
            replacement = choice["replaced_by"]
            if replacement not in choices:
                _fail(
                    "INVALID_REFERENCE",
                    path + ("choice_sets", identity, "choices", key, "replaced_by"),
                    "Replacement Choice does not resolve",
                )
            edges[("choice", f"{identity}#{key}")] = ("choice", f"{identity}#{replacement}")
    for mapping_name, mapping in (("fieldsets", graph.fieldsets), ("asset_types", graph.asset_types)):
        for identity, item in mapping.items():
            if "replaced_by" in item:
                target = _resolve(
                    mapping, item["replaced_by"], path + (mapping_name, identity, "replaced_by"), declared
                )
                if target is _EXTERNAL:
                    _fail(
                        "DEPENDENCY_GRAPH_UNAVAILABLE",
                        path + (mapping_name, identity, "replaced_by"),
                        "Replacement dependency definitions are not loaded",
                    )
                edges[(mapping_name, identity)] = (mapping_name, item["replaced_by"])
    states: dict[tuple[str, str], int] = {}

    def visit(node: tuple[str, str]) -> None:
        state = states.get(node, 0)
        if state == 1:
            _fail("REFERENCE_CYCLE", path, f"Replacement graph contains a cycle at {node[1]!r}")
        if state == 2:
            return
        states[node] = 1
        if node in edges:
            visit(edges[node])
        states[node] = 2

    for node in edges:
        visit(node)


def _choice_set_for_field(
    field: dict[str, Any], graph: _Graph, path: tuple[PathPart, ...], declared: frozenset[str]
) -> dict[str, Any] | object:
    return _resolve(graph.choice_sets, field["choice_set"], path + ("choice_set",), declared)


def _active_fields_for_type(
    asset_type: dict[str, Any],
    graph: _Graph,
    path: tuple[PathPart, ...],
    declared: frozenset[str],
    limits: ValidationLimits,
) -> dict[str, dict[str, Any]]:
    effective: dict[str, dict[str, Any]] = {
        key: field
        for key, field in graph.fields_by_key.items()
        if field["lifecycle"] == "active" and field["activation"] == "global" and "asset_type" in field["targets"]
    }
    for index, fieldset_ref in enumerate(asset_type["fieldsets"]):
        fieldset = _resolve(graph.fieldsets, fieldset_ref, path + ("fieldsets", index), declared)
        if fieldset is _EXTERNAL:
            _fail(
                "DEPENDENCY_GRAPH_UNAVAILABLE",
                path + ("fieldsets", index),
                "Referenced dependency definitions are not loaded",
            )
        if fieldset["lifecycle"] != "active":
            continue
        for field_index, field_ref in enumerate(fieldset["fields"]):
            field = _resolve(
                graph.fields_by_identity, field_ref, path + ("fieldsets", index, "fields", field_index), declared
            )
            if field is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("fieldsets", index, "fields", field_index),
                    "Referenced dependency definitions are not loaded",
                )
            if field["activation"] == "global":
                _fail(
                    "INVALID_APPLICABILITY",
                    path + ("fieldsets", index, "fields", field_index),
                    "Global Fields may not be placed in a Fieldset",
                )
            if "asset_type" not in field["targets"]:
                _fail(
                    "INVALID_APPLICABILITY",
                    path + ("fieldsets", index, "fields", field_index),
                    "Field does not apply to Asset Types",
                )
            if field["lifecycle"] == "active":
                effective.setdefault(field["key"], field)
    if len(effective) > limits.max_effective_fields_per_type:
        _fail("RESOURCE_LIMIT", path + ("fieldsets",), "Effective Asset Type Fields exceed the configured limit")
    return effective


def _value_present(field: dict[str, Any], value: Any) -> bool:
    if value is None:
        return False
    field_type = field["field_type"]
    if field_type in {"text", "date", "single-select", "decimal"}:
        return type(value) is str and value != ""
    if field_type == "multi-select":
        return type(value) is list and bool(value)
    if field_type == "integer":
        return type(value) is int
    if field_type == "boolean":
        return type(value) is bool
    return True


def _canonical_decimal_value(raw: str, scale: int, path: tuple[PathPart, ...]) -> str:
    if not isinstance(raw, str) or _DECIMAL_BOUND_RE.fullmatch(raw) is None:
        _fail("INVALID_TYPE", path, "Decimal values must be bounded base-10 strings")
    if raw.startswith("-") and Decimal(raw) == 0:
        _fail("INVALID_TYPE", path, "Negative zero is not allowed")
    fractional = raw.partition(".")[2]
    if len(fractional) > scale:
        _fail("INVALID_RANGE", path, "Decimal value has more fractional digits than its Field scale")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        _fail("INVALID_TYPE", path, "Decimal value is invalid")
        raise AssertionError from exc
    return format(parsed, f".{scale}f")


def _check_range(value: Decimal, validation: dict[str, Any], path: tuple[PathPart, ...]) -> None:
    for bound_name, comparator in (("minimum", value.__lt__), ("maximum", value.__gt__)):
        if bound_name in validation:
            bound = _decimal(validation[bound_name], path + (bound_name,))
            if comparator(bound):
                _fail("INVALID_RANGE", path, f"Value violates the {bound_name} bound")


def _validate_typed_value(  # noqa: C901 - seven fixed wire codecs share one error/path boundary
    field: dict[str, Any],
    value: Any,
    graph: _Graph,
    path: tuple[PathPart, ...],
    declared: frozenset[str],
    *,
    historical: bool,
) -> Any:
    if value is None:
        if field["required"] and not historical:
            _fail("REQUIRED_FIELD", path, "Required Fields may not be null or omitted")
        if not field["nullable"]:
            _fail("INVALID_TYPE", path, "Null is not allowed for this Field")
        return None
    field_type = field["field_type"]
    validation = field["validation"]
    if field_type == "text":
        value = _expect_string(value, path)
        if value == "" and not field["required"]:
            return value
        if len(value) > validation["max_length"]:
            _fail("INVALID_RANGE", path, "Text value exceeds max_length")
        if "regex" in validation and re.fullmatch(validation["regex"], value, flags=re.ASCII) is None:
            _fail("INVALID_VALUE", path, "Text value does not match regex")
        if validation.get("rule") == "rfc1123_hostname":
            labels = value.split(".")
            label_re = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
            if (
                not 1 <= len(value) <= 253
                or value.endswith(".")
                or any(label_re.fullmatch(label) is None for label in labels)
            ):
                _fail("INVALID_VALUE", path, "Text value is not an RFC 1123 hostname")
        return value
    if field_type == "integer":
        value = _expect_int(value, path, minimum=-9007199254740991, maximum=9007199254740991)
        _check_range(Decimal(value), validation, path)
        return value
    if field_type == "decimal":
        value = _canonical_decimal_value(value, validation["scale"], path)
        _check_range(Decimal(value), validation, path)
        return value
    if field_type == "boolean":
        return _expect_bool(value, path)
    if field_type == "date":
        value = _expect_string(value, path)
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            _fail("INVALID_VALUE", path, "Date must be an ISO calendar date")
            raise AssertionError from exc
        if parsed.isoformat() != value:
            _fail("INVALID_VALUE", path, "Date must use canonical ISO spelling")
        return value
    choice_set = _choice_set_for_field(field, graph, path, declared)
    if choice_set is _EXTERNAL:
        _fail("DEPENDENCY_GRAPH_UNAVAILABLE", path, "Referenced Choice Set definitions are not loaded")
    choices = {choice["key"]: choice for choice in choice_set["choices"]}
    if field_type == "single-select":
        value = _expect_string(value, path)
        choice = choices.get(value)
        if choice is None or (choice["lifecycle"] != "active" and not historical):
            _fail("INVALID_CHOICE", path, "Choice key is not available for this value")
        return value
    value = _expect_array(value, path)
    if len(value) > field["validation"]["max_values"]:
        _fail("INVALID_RANGE", path, "Too many Choice values")
    if len(set(value)) != len(value):
        _fail("DUPLICATE_VALUE", path, "Multi-choice values must be unique")
    for index, item in enumerate(value):
        item = _expect_string(item, path + (index,))
        choice = choices.get(item)
        if choice is None or (choice["lifecycle"] != "active" and not historical):
            _fail("INVALID_CHOICE", path + (index,), "Choice key is not available for this value")
    return sorted(value)


def _validate_cross_field_rules(
    values: dict[str, Any], fields_by_key: dict[str, dict[str, Any]], path: tuple[PathPart, ...]
) -> None:
    for key, field in fields_by_key.items():
        rule = field["validation"].get("rule")
        if rule == "temperature_max_gte_min":
            other = values.get("operating_temperature_min")
            current = values.get(key)
            if other is not None and current is not None and Decimal(current) < Decimal(other):
                _fail("INVALID_RANGE", path + (key,), "Maximum temperature must not be below minimum")
        elif rule == "voltage_max_gte_min":
            other = values.get("input_voltage_min")
            current = values.get(key)
            if other is not None and current is not None and Decimal(current) < Decimal(other):
                _fail("INVALID_RANGE", path + (key,), "Maximum voltage must not be below minimum")
        elif rule == "runtime_requires_load":
            runtime = values.get("battery_runtime")
            if runtime is not None and values.get("battery_runtime_load") in (None, ""):
                _fail("REQUIRED_FIELD", path + ("battery_runtime_load",), "Battery runtime requires an explicit load")


def _validate_asset_type_graph(  # noqa: C901 - complete Type graph validation is intentionally coordinated
    asset_type: dict[str, Any],
    graph: _Graph,
    path: tuple[PathPart, ...],
    ctx: _ValidationContext,
    declared: frozenset[str],
) -> None:
    category = asset_type["category"]
    if category is not None:
        category_row = _resolve_catalog(graph.categories, category, path + ("category",))
        if category_row is not _EXTERNAL and "asset" not in category_row["applies_to"]:
            _fail("INVALID_APPLICABILITY", path + ("category",), "Category does not apply to Assets")
    _resolve_catalog(graph.manufacturers, asset_type["manufacturer"], path + ("manufacturer",))
    effective = _active_fields_for_type(asset_type, graph, path, declared, ctx.limits)
    specifications = asset_type["specifications"]
    normalized_values: dict[str, Any] = {}
    for key, value in specifications.items():
        field = graph.fields_by_key.get(key)
        if field is None:
            _fail("UNKNOWN_FIELD_KEY", path + ("specifications", key), "Specification Field key does not resolve")
        if "asset_type" not in field["targets"]:
            _fail("INVALID_APPLICABILITY", path + ("specifications", key), "Field does not apply to Asset Types")
        if key not in effective:
            _fail(
                "INVALID_APPLICABILITY",
                path + ("specifications", key),
                "Field is not in the effective Asset Type composition",
            )
        if field["lifecycle"] != "active":
            _fail("INVALID_VALUE", path + ("specifications", key), "Deprecated Fields cannot receive current values")
        normalized_values[key] = _validate_typed_value(
            field, value, graph, path + ("specifications", key), declared, historical=False
        )
    for key, field in effective.items():
        if field["required"] and not _value_present(field, normalized_values.get(key)):
            _fail("REQUIRED_FIELD", path + ("specifications", key), "Required Field is missing")
    _validate_cross_field_rules(normalized_values, effective, path + ("specifications",))
    for key, record in asset_type["historical_specifications"].items():
        field = graph.fields_by_key.get(key)
        if field is None:
            _fail(
                "INVALID_REFERENCE", path + ("historical_specifications", key), "Historical Field key does not resolve"
            )
        _validate_typed_value(
            field,
            record["value"],
            graph,
            path + ("historical_specifications", key, "value"),
            declared,
            historical=True,
        )


def _validate_graph(  # noqa: C901 - cross-definition graph checks run in one deterministic pass
    graph: _Graph, path: tuple[PathPart, ...], ctx: _ValidationContext, declared: frozenset[str]
) -> None:
    _validate_replacements(graph, path, declared)
    for identity, field in graph.fields_by_identity.items():
        if "choice_set" in field:
            choice_set = _choice_set_for_field(field, graph, path + ("fields", identity), declared)
            if choice_set is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("fields", identity, "choice_set"),
                    "Referenced Choice Set definitions are not loaded",
                )
            if field["lifecycle"] == "active":
                active_choices = [choice for choice in choice_set["choices"] if choice["lifecycle"] == "active"]
                if choice_set["lifecycle"] != "active" or not active_choices:
                    _fail(
                        "DEPENDENCY_RETIREMENT",
                        path + ("fields", identity),
                        "Active select Fields need an active Choice Set",
                    )
    for identity, fieldset in graph.fieldsets.items():
        for index, field_ref in enumerate(fieldset["fields"]):
            field = _resolve(
                graph.fields_by_identity, field_ref, path + ("fieldsets", identity, "fields", index), declared
            )
            if field is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("fieldsets", identity, "fields", index),
                    "Referenced dependency Fields are not loaded",
                )
            if field["activation"] == "global":
                _fail(
                    "INVALID_APPLICABILITY",
                    path + ("fieldsets", identity, "fields", index),
                    "Global Fields may not be placed in a Fieldset",
                )
            if (
                fieldset["lifecycle"] == "active"
                and "asset_type" not in field["targets"]
                and "asset" not in field["targets"]
            ):
                _fail(
                    "INVALID_APPLICABILITY",
                    path + ("fieldsets", identity, "fields", index),
                    "Field has no supported target",
                )
    for identity, category in graph.categories.items():
        for index, fieldset_ref in enumerate(category["default_fieldsets"]):
            resolved = _resolve(
                graph.fieldsets, fieldset_ref, path + ("categories", identity, "default_fieldsets", index), declared
            )
            if resolved is _EXTERNAL:
                _fail(
                    "DEPENDENCY_GRAPH_UNAVAILABLE",
                    path + ("categories", identity, "default_fieldsets", index),
                    "Referenced dependency Fieldsets are not loaded",
                )
    for identity, asset_type in graph.asset_types.items():
        _validate_asset_type_graph(asset_type, graph, path + ("asset_types", identity), ctx, declared)


def _validate_requirements(
    release_value: Any, path: tuple[PathPart, ...], owner_namespace: str, ctx: _ValidationContext
) -> frozenset[str]:
    requirements = _expect_array(release_value, path)
    if len(requirements) > ctx.limits.max_dependencies:
        _fail("RESOURCE_LIMIT", path, "Too many dependencies")
    ctx.count("dependencies", len(requirements), path)
    seen: set[tuple[str, int]] = set()
    namespaces: set[str] = set()
    for index, value in enumerate(requirements):
        requirement = _check_properties(
            value,
            path + (index,),
            required=("namespace", "release", "digest"),
            optional=(),
        )
        namespace = _check_pattern(
            requirement["namespace"], _NAMESPACE_RE, path + (index, "namespace"), "Invalid dependency namespace"
        )
        release = _expect_int(requirement["release"], path + (index, "release"), minimum=1, maximum=9007199254740991)
        digest = _expect_string(requirement["digest"], path + (index, "digest"))
        if _DIGEST_RE.fullmatch(digest) is None:
            _fail("INVALID_IDENTITY", path + (index, "digest"), "Dependency digest must be sha256 hex")
        if namespace == owner_namespace:
            _fail("REFERENCE_CYCLE", path + (index,), "A release may not depend on itself")
        identity = (namespace, release)
        if identity in seen:
            _fail("DUPLICATE_DEPENDENCY", path + (index,), "Dependency identities must be unique")
        seen.add(identity)
        namespaces.add(namespace)
        installed = ctx.installed_dependencies.get(identity)
        if installed is None:
            _fail("DEPENDENCY_MISSING", path + (index,), f"Dependency {namespace}/{release} is not installed")
        if installed.digest != digest:
            _fail(
                "DEPENDENCY_DIGEST_MISMATCH",
                path + (index, "digest"),
                "Dependency digest does not match installed content",
            )
    return frozenset(namespaces)


def _validate_library_header(value: Any, path: tuple[PathPart, ...]) -> tuple[str, int]:
    library = _check_properties(value, path, required=("namespace", "release", "label"), optional=())
    namespace = _check_pattern(library["namespace"], _NAMESPACE_RE, path + ("namespace",), "Invalid library namespace")
    if namespace == "catalog":
        _fail("NAMESPACE_TAKEOVER", path + ("namespace",), "catalog is reserved for shared catalogue references")
    release = _expect_int(library["release"], path + ("release",), minimum=1, maximum=9007199254740991)
    _expect_string(library["label"], path + ("label",), nonempty=True)
    if len(library["label"]) > 200:
        _fail("INVALID_RANGE", path + ("label",), "Library labels may not exceed 200 characters")
    return namespace, release


def _validate_release_root(value: Any, path: tuple[PathPart, ...], ctx: _ValidationContext) -> _ValidatedPart:
    root = _check_properties(
        value,
        path,
        required=("schema_version", "kind", "library", "requires", "definitions"),
        optional=(),
    )
    if root["schema_version"] != 1 or type(root["schema_version"]) is not int:
        _fail("UNSUPPORTED_VERSION", path + ("schema_version",), "Only schema_version 1 is supported")
    if root["kind"] != "itambox.type-library.release":
        _fail("UNSUPPORTED_KIND", path + ("kind",), "Expected a Type Library release")
    namespace, release = _validate_library_header(root["library"], path + ("library",))
    declared = _validate_requirements(root["requires"], path + ("requires",), namespace, ctx)
    graph = _validate_definitions(root["definitions"], path + ("definitions",), namespace, ctx, declared)
    return _ValidatedPart(namespace, release, graph, declared)


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _semantic_value(child)) for key, child in value.items()))
    if isinstance(value, list):
        return tuple(_semantic_value(child) for child in value)
    return value


def _immutable_signature(kind: str, value: dict[str, Any]) -> Any:
    candidate = deepcopy(value)
    if kind == "field":
        candidate.pop("label", None)
        candidate.pop("help_text", None)
        candidate.pop("lifecycle", None)
        candidate.pop("replaced_by", None)
        candidate["targets"] = sorted(candidate["targets"])
    elif kind == "choice_set":
        candidate.pop("label", None)
        candidate.pop("description", None)
        candidate.pop("lifecycle", None)
        candidate.pop("replaced_by", None)
        candidate.pop("choices", None)
    elif kind == "choice":
        candidate.pop("label", None)
        candidate.pop("lifecycle", None)
        candidate.pop("replaced_by", None)
    elif kind == "fieldset":
        candidate.pop("label", None)
        candidate.pop("description", None)
        candidate.pop("lifecycle", None)
        candidate.pop("replaced_by", None)
    elif kind == "category":
        candidate.pop("label", None)
        candidate.pop("description", None)
        candidate.pop("lifecycle", None)
        candidate["applies_to"] = sorted(candidate["applies_to"])
    elif kind == "manufacturer":
        candidate.pop("label", None)
        candidate.pop("description", None)
        candidate.pop("lifecycle", None)
    elif kind == "asset_type":
        for name in (
            "configuration",
            "description",
            "lifecycle",
            "specifications",
            "historical_specifications",
            "replaced_by",
        ):
            candidate.pop(name, None)
    return _semantic_value(candidate)


def _snapshot_extra_is_allowed(
    kind: str,
    identity: str,
    value: dict[str, Any],
    upstream: _ValidatedPart,
) -> bool:
    if kind in {"category", "manufacturer"}:
        return False
    namespace = identity.partition("/")[0]
    if namespace == "itambox" and namespace != upstream.namespace:
        return False
    if namespace in upstream.declared_dependencies:
        return False
    if namespace == upstream.namespace:
        return value["lifecycle"] == "deprecated"
    return True


def _validate_snapshot_consistency(  # noqa: C901 - source/effective closure is one deterministic comparison
    upstream: _ValidatedPart, effective: _Graph, path: tuple[PathPart, ...]
) -> None:
    for kind, upstream_mapping, effective_mapping in (
        ("choice_set", upstream.graph.choice_sets, effective.choice_sets),
        ("field", upstream.graph.fields_by_identity, effective.fields_by_identity),
        ("fieldset", upstream.graph.fieldsets, effective.fieldsets),
        ("category", upstream.graph.categories, effective.categories),
        ("manufacturer", upstream.graph.manufacturers, effective.manufacturers),
        ("asset_type", upstream.graph.asset_types, effective.asset_types),
    ):
        for identity, source in upstream_mapping.items():
            target = effective_mapping.get(identity)
            if target is None:
                _fail("HISTORICAL_CLOSURE", path, f"Snapshot omits upstream {kind} identity {identity!r}")
            if _immutable_signature(kind, source) != _immutable_signature(kind, target):
                _fail("IMMUTABLE_DEFINITION", path, f"Snapshot changes immutable {kind} identity {identity!r}")
            if kind == "choice_set":
                source_choices = {choice["key"]: choice for choice in source["choices"]}
                target_choices = {choice["key"]: choice for choice in target["choices"]}
                if not source_choices.keys() <= target_choices.keys():
                    _fail("HISTORICAL_CLOSURE", path, f"Snapshot omits upstream Choice in {identity!r}")
                for key, choice in target_choices.items():
                    if key not in source_choices and choice["lifecycle"] != "deprecated":
                        _fail("NAMESPACE_TAKEOVER", path, f"Snapshot adds active Choice {identity}#{key!r}")
                    if key in source_choices and _immutable_signature("choice", choice) != _immutable_signature(
                        "choice", source_choices[key]
                    ):
                        _fail("IMMUTABLE_DEFINITION", path, f"Snapshot changes Choice identity {identity}#{key!r}")
        for identity, value in effective_mapping.items():
            if identity not in upstream_mapping:
                if not _snapshot_extra_is_allowed(kind, identity, value, upstream):
                    _fail("NAMESPACE_TAKEOVER", path, f"Snapshot adds unmanaged {kind} identity {identity!r}")


def _normalize_decimal_bound(raw: str, *, scale: int | None) -> str:
    value = Decimal(raw)
    if scale is not None:
        return format(value, f".{scale}f")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _normalize_field(field: dict[str, Any]) -> None:
    field["targets"] = sorted(field["targets"])
    validation = field["validation"]
    scale = validation.get("scale") if field["field_type"] == "decimal" else None
    for name in ("minimum", "maximum"):
        if name in validation:
            validation[name] = _normalize_decimal_bound(validation[name], scale=scale)


def _normalize_value(field: dict[str, Any], value: Any) -> Any:
    if field["field_type"] == "decimal" and value is not None:
        return _canonical_decimal_value(value, field["validation"]["scale"], ())
    if field["field_type"] == "multi-select" and value is not None:
        return sorted(value)
    return value


def _normalize_definitions(definitions: dict[str, Any], graph: _Graph) -> None:
    for field in definitions["fields"]:
        _normalize_field(field)
    for category in definitions["categories"]:
        category["applies_to"] = sorted(category["applies_to"])
    definitions["choice_sets"] = sorted(definitions["choice_sets"], key=lambda item: item["id"])
    definitions["fields"] = sorted(definitions["fields"], key=lambda item: f"{item['namespace']}/{item['key']}")
    definitions["fieldsets"] = sorted(definitions["fieldsets"], key=lambda item: item["id"])
    definitions["categories"] = sorted(definitions["categories"], key=lambda item: item["id"])
    definitions["manufacturers"] = sorted(definitions["manufacturers"], key=lambda item: item["id"])
    definitions["asset_types"] = sorted(definitions["asset_types"], key=lambda item: item["id"])
    for asset_type in definitions["asset_types"]:
        specifications = asset_type["specifications"]
        asset_type["specifications"] = {
            key: _normalize_value(graph.fields_by_key[key], specifications[key]) for key in sorted(specifications)
        }
        history = asset_type["historical_specifications"]
        asset_type["historical_specifications"] = {
            key: {
                "value": _normalize_value(graph.fields_by_key[key], history[key]["value"]),
                "reason": history[key]["reason"],
            }
            for key in sorted(history)
        }


def _normalize_release(root: dict[str, Any], graph: _Graph) -> None:
    root["requires"] = sorted(root["requires"], key=lambda item: (item["namespace"], item["release"], item["digest"]))
    _normalize_definitions(root["definitions"], graph)


def _validated_installed_dependencies(
    installed_dependencies: Mapping[Any, Any] | Iterable[InstalledDependency] | None,
) -> tuple[InstalledDependency, ...]:
    dependencies = _coerce_installed_dependencies(installed_dependencies)
    for dependency in dependencies:
        if _NAMESPACE_RE.fullmatch(dependency.namespace) is None:
            raise issue("INVALID_IDENTITY", (), "Installed dependency namespace is invalid")
        if type(dependency.release) is not int or dependency.release < 1:
            raise issue("INVALID_IDENTITY", (), "Installed dependency release is invalid")
        if _DIGEST_RE.fullmatch(dependency.digest) is None:
            raise issue("INVALID_IDENTITY", (), "Installed dependency digest is invalid")
    return dependencies


def validate_library_document(
    document: bytes | str,
    *,
    installed_dependencies: Mapping[Any, Any] | Iterable[InstalledDependency] | None = None,
    limits: ValidationLimits | Mapping[str, int] | None = None,
) -> ValidatedLibraryDocument:
    """Validate, normalize, and JCS-hash a complete release or snapshot.

    ``installed_dependencies`` is a server-supplied exact identity index. The
    validator never fetches or creates dependency rows. Dependencies that are
    referenced by a Field/Fieldset also need their graph loaded by the caller;
    otherwise complete semantic validation fails closed with
    ``DEPENDENCY_GRAPH_UNAVAILABLE``.
    """

    resolved_limits = ValidationLimits.from_value(limits)
    dependencies = _validated_installed_dependencies(installed_dependencies)
    ctx = _ValidationContext(resolved_limits, dependencies)
    parsed = parse_json_document(document, limits=resolved_limits)
    kind = parsed.get("kind")
    if kind == "itambox.type-library.release":
        validated = _validate_release_root(parsed, (), ctx)
        _normalize_release(parsed, validated.graph)
    elif kind == "itambox.type-library.snapshot":
        root = _check_properties(
            parsed,
            (),
            required=("schema_version", "kind", "upstream", "effective_definitions"),
            optional=(),
        )
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            _fail("UNSUPPORTED_VERSION", ("schema_version",), "Only schema_version 1 is supported")
        upstream = _validate_release_root(root["upstream"], ("upstream",), ctx)
        effective = _validate_definitions(
            root["effective_definitions"],
            ("effective_definitions",),
            upstream.namespace,
            ctx,
            upstream.declared_dependencies,
            allow_additional_namespaces=True,
        )
        _validate_snapshot_consistency(upstream, effective, ("effective_definitions",))
        _normalize_release(root["upstream"], upstream.graph)
        _normalize_definitions(root["effective_definitions"], effective)
        validated = upstream
    else:
        _fail("UNSUPPORTED_KIND", ("kind",), "Expected a Type Library release or snapshot")
    try:
        canonical_bytes = canonicalize_release_document(parsed)
    except (TypeError, ValueError, OverflowError) as exc:
        raise issue("JCS_ERROR", (), "Normalized document could not be RFC 8785 canonicalized") from exc
    semantic_digest = "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
    return ValidatedLibraryDocument(
        kind=kind, normalized_document=parsed, canonical_bytes=canonical_bytes, semantic_digest=semantic_digest
    )


def normalize_library_document(
    document: bytes | str,
    *,
    installed_dependencies: Mapping[Any, Any] | Iterable[InstalledDependency] | None = None,
    limits: ValidationLimits | Mapping[str, int] | None = None,
) -> ValidatedLibraryDocument:
    """Explicit spelling for callers that need the normalized/JCS result."""

    return validate_library_document(
        document,
        installed_dependencies=installed_dependencies,
        limits=limits,
    )


__all__ = [
    "DependencyReference",
    "InstalledDependency",
    "LibraryValidationError",
    "ValidatedLibraryDocument",
    "ValidationIssue",
    "ValidationLimits",
    "normalize_library_document",
    "validate_library_document",
]
