"""PostgreSQL-safe queryset consumers for source-qualified specification filters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha1
from typing import TYPE_CHECKING

from django.db.models import Case, CharField, DateField, DecimalField, F, Func, IntegerField, Q, Value, When
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast

from .contracts import FieldFilter, FieldReference

if TYPE_CHECKING:
    from django.db.models.query import QuerySet


_INTEGER_RE = r"^-?(?:0|[1-9][0-9]*)$"
_DECIMAL_RE = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
_DATE_RE = r"^[1-9][0-9]{3}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$"


class SpecificationQueryError(ValueError):
    """Raised when a consumer cannot safely compile a specification filter."""


def scope_queryset(queryset: QuerySet, tenant_ids: Iterable[int], *, tenant_field: str = "tenant_id") -> QuerySet:
    """Apply the tenant boundary before any value expression is introduced.

    An empty authorization result fails closed.  This helper intentionally does
    not turn an empty tenant set into a global query.
    """

    normalized = tuple(tenant_ids)
    if any(type(tenant_id) is not int or tenant_id <= 0 for tenant_id in normalized):
        raise SpecificationQueryError("tenant_ids must contain positive integers")
    if not normalized:
        return queryset.none()
    criteria = {f"{tenant_field}__in": normalized}
    if tenant_field == "tenant_id":
        criteria["tenant__deleted_at__isnull"] = True
    return queryset.filter(**criteria)


def _query_target(queryset: QuerySet) -> str:
    model_name = getattr(getattr(queryset, "model", None), "_meta", None)
    model_name = getattr(model_name, "model_name", None)
    if model_name not in {"asset", "assettype"}:
        raise SpecificationQueryError("specification filters support Asset and AssetType querysets only")
    return model_name


def _json_path(queryset: QuerySet, reference: FieldReference) -> str:
    target = _query_target(queryset)
    if target == "asset":
        return "custom_field_data" if reference.source == "asset" else "asset_type__custom_field_data"
    if reference.source != "asset_type":
        raise SpecificationQueryError("an Asset source cannot be applied to an AssetType queryset")
    return "custom_field_data"


def _type_path(queryset: QuerySet) -> str:
    return "asset_type" if _query_target(queryset) == "asset" else ""


def _current_applicability(queryset: QuerySet, reference: FieldReference, definition: object | None):
    """Return a Q expression for the current composed definition, if knowable."""

    if definition is None:
        return None
    targets = getattr(definition, "targets", frozenset())
    if reference.source not in targets:
        return Q(pk__in=[])
    if getattr(definition, "lifecycle", "active") != "active":
        return Q(pk__in=[])
    if getattr(definition, "activation", "composed") == "global":
        return Q()
    prefix = _type_path(queryset)
    relation = f"{prefix + '__' if prefix else ''}fieldset_memberships__fieldset__field_memberships__custom_field"
    return Q(
        **{
            f"{relation}__name": reference.key,
            f"{relation}__lifecycle": "active",
            f"{relation}__activation": "composed",
        }
    )


def _aliases(reference: FieldReference, json_path: str) -> tuple[str, str]:
    digest = sha1(f"{reference.column_id}:{json_path}".encode("ascii")).hexdigest()[:12]
    return f"_t23_spec_text_{digest}", f"_t23_spec_type_{digest}"


def _annotate_value(queryset: QuerySet, reference: FieldReference, json_path: str) -> tuple[QuerySet, str, str]:
    text_alias, type_alias = _aliases(reference, json_path)
    return (
        queryset.annotate(
            **{
                text_alias: KeyTextTransform(reference.key, F(json_path)),
                type_alias: Func(
                    KeyTransform(reference.key, F(json_path)),
                    function="jsonb_typeof",
                    output_field=CharField(),
                ),
            }
        ),
        text_alias,
        type_alias,
    )


def _type_q(text_alias: str, type_alias: str, field_type: str | None):
    if field_type == "integer":
        return Q(**{type_alias: "number", f"{text_alias}__regex": _INTEGER_RE})
    if field_type == "decimal":
        return Q(**{type_alias: "string", f"{text_alias}__regex": _DECIMAL_RE})
    if field_type == "date":
        return Q(**{type_alias: "string", f"{text_alias}__regex": _DATE_RE})
    if field_type == "boolean":
        return Q(**{type_alias: "boolean"})
    if field_type in {"text", "single_select"}:
        return Q(**{type_alias: "string"})
    if field_type == "multi_select":
        return Q(**{type_alias: "array"})
    return Q(**{f"{text_alias}__isnull": False})


def _field_type(definition: object | None, filter_spec: FieldFilter) -> str | None:
    if definition is not None:
        return getattr(definition, "field_type", None)
    if filter_spec.operator in {"gt", "gte", "lt", "lte"}:
        if type(filter_spec.value) is int:
            return "integer"
        if type(filter_spec.value) is str:
            return "decimal"
    if filter_spec.operator == "contains_any" or filter_spec.operator == "contains_all":
        return "multi_select"
    return "text"


def _numeric_annotation(queryset: QuerySet, text_alias: str, *, integer: bool) -> tuple[QuerySet, str]:
    alias = f"{text_alias}_number"
    regex = _INTEGER_RE if integer else _DECIMAL_RE
    expression = Case(
        When(
            **{f"{text_alias}__regex": regex}, then=Cast(F(text_alias), DecimalField(max_digits=48, decimal_places=12))
        ),
        default=Value(None),
        output_field=DecimalField(max_digits=48, decimal_places=12),
    )
    return queryset.annotate(**{alias: expression}), alias


def _date_annotation(queryset: QuerySet, text_alias: str) -> tuple[QuerySet, str, str]:
    """Parse only ISO-shaped text and retain a normalized rendering guard.

    PostgreSQL ``to_date`` is used rather than an unguarded ``::date`` cast;
    comparing its round-trip rendering rejects overflow dates such as
    2024-02-31 without raising on malformed stored JSON.
    """

    raw_alias = f"{text_alias}_date"
    rendered_alias = f"{text_alias}_date_text"
    raw = Case(
        When(
            **{f"{text_alias}__regex": _DATE_RE},
            then=Func(F(text_alias), Value("YYYY-MM-DD"), function="to_date", output_field=DateField()),
        ),
        default=Value(None),
        output_field=DateField(),
    )
    queryset = queryset.annotate(**{raw_alias: raw})
    rendered = Func(F(raw_alias), Value("YYYY-MM-DD"), function="to_char", output_field=CharField())
    return queryset.annotate(**{rendered_alias: rendered}), raw_alias, rendered_alias


def _expected_date(value: object) -> date:
    if type(value) is not str:
        raise SpecificationQueryError("date comparisons require an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SpecificationQueryError("date comparisons require a valid ISO date") from exc


def _expected_decimal(value: object) -> Decimal:
    if type(value) is bool:
        raise SpecificationQueryError("numeric comparisons do not accept booleans")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SpecificationQueryError("numeric comparisons require a decimal value") from exc


def _value_condition(
    queryset: QuerySet,
    reference: FieldReference,
    filter_spec: FieldFilter,
    json_path: str,
    text_alias: str,
    type_alias: str,
    definition: object | None,
):
    field_type = _field_type(definition, filter_spec)
    operator = filter_spec.operator
    key_lookup = f"{json_path}__has_key"
    presence = Q(**{key_lookup: reference.key})
    if operator == "is_missing":
        return queryset.filter(~presence)
    if operator == "is_null":
        return queryset.filter(presence, **{type_alias: "null"})
    if operator == "is_empty":
        array_alias = f"{text_alias}_array_length"
        array_length = Case(
            When(
                **{type_alias: "array"},
                then=Func(
                    KeyTransform(reference.key, F(json_path)),
                    function="jsonb_array_length",
                    output_field=IntegerField(),
                ),
            ),
            default=Value(None),
            output_field=IntegerField(),
        )
        queryset = queryset.annotate(**{array_alias: array_length})
        return queryset.filter(presence).filter(Q(**{text_alias: ""}) | Q(**{array_alias: 0}))

    type_guard = _type_q(text_alias, type_alias, field_type)
    if operator in {"contains_any", "contains_all"}:
        if not isinstance(filter_spec.value, (list, tuple)) or not filter_spec.value:
            raise SpecificationQueryError(f"{operator} requires a non-empty sequence")
        if field_type != "multi_select":
            raise SpecificationQueryError(f"{operator} requires a multi-select definition")
        values = list(filter_spec.value)
        return queryset.filter(type_guard, presence, **{f"{json_path}__{reference.key}__contains": values})

    if operator == "contains":
        if type(filter_spec.value) is not str:
            raise SpecificationQueryError("text contains requires a string")
        return queryset.filter(type_guard, presence, **{f"{text_alias}__icontains": filter_spec.value})

    if field_type in {"integer", "decimal"}:
        queryset, number_alias = _numeric_annotation(queryset, text_alias, integer=field_type == "integer")
        expected = _expected_decimal(filter_spec.value)
        return queryset.filter(type_guard, presence, **{f"{number_alias}__{operator}": expected})

    if field_type == "date":
        queryset, date_alias, rendered_alias = _date_annotation(queryset, text_alias)
        expected = _expected_date(filter_spec.value)
        return queryset.filter(
            type_guard, presence, **{rendered_alias: F(text_alias), f"{date_alias}__{operator}": expected}
        )

    if field_type == "boolean":
        if type(filter_spec.value) is not bool or operator not in {"eq", "neq"}:
            raise SpecificationQueryError("boolean specifications support strict equality only")
        condition = Q(**{text_alias: "true" if filter_spec.value else "false"})
        if operator == "neq":
            condition = ~condition
        return queryset.filter(type_guard, presence & condition)

    if operator not in {"eq", "neq", "gt", "gte", "lt", "lte"}:
        raise SpecificationQueryError(f"operator {operator!r} is not valid for this field type")
    if operator in {"gt", "gte", "lt", "lte"}:
        raise SpecificationQueryError("ordered comparisons require integer, decimal, or date definitions")
    expected = filter_spec.value
    if type(expected) is not str:
        raise SpecificationQueryError("text and Choice equality require a string")
    condition = Q(**{text_alias: expected})
    if operator == "neq":
        condition = ~condition
    return queryset.filter(type_guard, presence & condition)


def apply_specification_filter(
    queryset: QuerySet,
    filter_spec: FieldFilter,
    *,
    tenant_ids: Iterable[int] | None = None,
    tenant_field: str = "tenant_id",
    field_definition: object | None = None,
) -> QuerySet:
    """Apply one explicit specification filter to a scoped queryset.

    ``tenant_ids`` is applied first when supplied.  Passing ``None`` means the
    caller has already supplied an authorized queryset (for example a
    tenant-scoped manager); it never causes a global fallback.
    """

    if not isinstance(filter_spec, FieldFilter):
        raise TypeError("filter_spec must be a FieldFilter")
    scoped = queryset if tenant_ids is None else scope_queryset(queryset, tenant_ids, tenant_field=tenant_field)
    json_path = _json_path(scoped, filter_spec.reference)
    if field_definition is not None:
        definition_key = str(getattr(field_definition, "key", ""))
        if definition_key != filter_spec.reference.key:
            raise SpecificationQueryError("field definition key does not match the source-qualified reference")
        targets = getattr(field_definition, "targets", frozenset())
        if filter_spec.reference.source not in targets:
            return scoped.none()

    applicability = _current_applicability(scoped, filter_spec.reference, field_definition)
    if filter_spec.status == "history":
        if applicability is None or not applicability.children:
            return scoped.none()
        scoped = scoped.filter(~applicability)
    elif filter_spec.status == "current":
        if applicability is not None:
            scoped = scoped.filter(applicability)
    elif filter_spec.status == "unknown":
        if field_definition is not None:
            return scoped.none()
    elif filter_spec.status == "invalid" and field_definition is None:
        return scoped.none()

    has_key = Q(**{f"{json_path}__has_key": filter_spec.reference.key})
    if filter_spec.status == "unknown":
        return scoped.filter(has_key)

    scoped, text_alias, type_alias = _annotate_value(scoped, filter_spec.reference, json_path)
    if filter_spec.status == "invalid":
        field_type = _field_type(field_definition, filter_spec)
        valid = _type_q(text_alias, type_alias, field_type)
        return scoped.filter(has_key).filter(~valid)
    return _value_condition(
        scoped, filter_spec.reference, filter_spec, json_path, text_alias, type_alias, field_definition
    )


def apply_specification_filters(
    queryset: QuerySet,
    filters: Iterable[FieldFilter],
    *,
    tenant_ids: Iterable[int] | None = None,
    tenant_field: str = "tenant_id",
    definitions: dict[FieldReference, object] | None = None,
) -> QuerySet:
    """Apply filters in order, preserving the caller's explicit source map."""

    result = queryset
    first = True
    for filter_spec in filters:
        result = apply_specification_filter(
            result,
            filter_spec,
            tenant_ids=tenant_ids if first else None,
            tenant_field=tenant_field,
            field_definition=(definitions or {}).get(filter_spec.reference),
        )
        first = False
    return result
