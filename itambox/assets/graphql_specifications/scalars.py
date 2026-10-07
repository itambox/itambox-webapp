"""GraphQL transport scalars for the specification read contract.

The domain codecs remain the authority for field-specific validation.  These
scalars only enforce transport shape and the protocol-wide integer envelope;
in particular, Decimal is deliberately a string scalar so fixed scale is not
lost through a JSON number or a GraphQL Float.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import NewType

import strawberry
from graphql import GraphQLError
from strawberry.scalars import JSON

from extras.services.specifications.codecs import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN


def _scalar_error(name: str, value: object) -> GraphQLError:
    return GraphQLError(f"{name} cannot represent value: {value!r}")


def _safe_integer(value: object) -> int:
    if type(value) is not int:
        raise _scalar_error("SafeInteger", value)
    if not SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX:
        raise _scalar_error("SafeInteger", value)
    return value


def _decimal_text(value: object) -> str:
    if type(value) is not str:
        raise _scalar_error("Decimal", value)
    return value


def _decimal_serialize(value: object) -> str:
    # Model Decimal columns (costs) serialize as their exact string; specification
    # values are already strings.
    if isinstance(value, Decimal):
        return str(value)
    return _decimal_text(value)


def _date_text(value: object) -> str:
    if type(value) is not str:
        raise _scalar_error("Date", value)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise _scalar_error("Date", value) from exc
    return value


def _date_serialize(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return _date_text(value)


def _revision_text(value: object) -> str:
    if type(value) is not str or not value:
        raise _scalar_error("CategoryDefaultSnapshotRevision", value)
    return value


def _cursor_text(value: object) -> str:
    if type(value) is not str or not value:
        raise _scalar_error("Cursor", value)
    return value


def _json_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool, float}:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    raise _scalar_error("JSON", value)


SafeInteger = strawberry.scalar(
    NewType("SafeInteger", int),
    name="SafeInteger",
    description="An integer in the JavaScript safe-integer range.",
    serialize=_safe_integer,
    parse_value=_safe_integer,
)
DecimalScalar = strawberry.scalar(
    NewType("DecimalScalar", str),
    name="Decimal",
    description="A decimal transported as a string to preserve fixed scale.",
    serialize=_decimal_serialize,
    parse_value=_decimal_text,
)
DateScalar = strawberry.scalar(
    NewType("DateScalar", str),
    name="Date",
    description="An ISO-8601 calendar date.",
    serialize=_date_serialize,
    parse_value=_date_text,
)
CategoryDefaultSnapshotRevision = strawberry.scalar(
    NewType("CategoryDefaultSnapshotRevision", str),
    name="CategoryDefaultSnapshotRevision",
    description="Opaque non-empty Category-default snapshot revision.",
    serialize=_revision_text,
    parse_value=_revision_text,
)
CursorScalar = strawberry.scalar(
    NewType("CursorScalar", str),
    name="Cursor",
    description="Opaque, non-empty cursor text used by bounded connections.",
    serialize=_cursor_text,
    parse_value=_cursor_text,
)
JSONScalar = strawberry.scalar(
    NewType("JSONScalar", object),
    name="JSON",
    description="Read-only JSON value transport for uninterpreted history.",
    serialize=_json_value,
    parse_value=_json_value,
)

# Python types the schema maps onto the transport scalars above.
# ``JSON`` replaces Strawberry's built-in JSON scalar so the read-only
# transport keeps rejecting values the GraphQL JSON model cannot carry.
SCALAR_OVERRIDES = {date: DateScalar, Decimal: DecimalScalar, JSON: JSONScalar}

__all__ = [
    "CategoryDefaultSnapshotRevision",
    "CursorScalar",
    "DateScalar",
    "DecimalScalar",
    "JSONScalar",
    "SAFE_INTEGER_MAX",
    "SAFE_INTEGER_MIN",
    "SCALAR_OVERRIDES",
    "SafeInteger",
]
