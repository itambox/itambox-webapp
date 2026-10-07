"""Transport scalars shared by the read-only GraphQL schema.

``BigInt`` and ``JSONString`` keep the wire contract of the graphemes scalars
this schema published before the Strawberry port.
"""

from __future__ import annotations

import json
from typing import NewType

import strawberry
from graphql import GraphQLError

__all__ = ["BigInt", "JSONString"]


def _coerce_int(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        raise GraphQLError(f"BigInt cannot represent value: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GraphQLError(f"BigInt cannot represent value: {value!r}") from exc


def _serialize_json_string(value: object) -> str:
    return json.dumps(value)


def _parse_json_string(value: object) -> object:
    if not isinstance(value, str):
        raise GraphQLError(f"JSONString cannot represent value: {value!r}")
    try:
        return json.loads(value)
    except ValueError as exc:
        raise GraphQLError(f"Badly formed JSONString: {exc}") from exc


BigInt = strawberry.scalar(
    NewType("BigInt", int),
    name="BigInt",
    description=(
        "The `BigInt` scalar type represents non-fractional whole numeric values.\n"
        "`BigInt` is not constrained to 32-bit like the `Int` type and thus is a less\n"
        "compatible type."
    ),
    serialize=_coerce_int,
    parse_value=_coerce_int,
)

JSONString = strawberry.scalar(
    NewType("JSONString", object),
    name="JSONString",
    description=(
        "Allows use of a JSON String for input / output from the GraphQL schema.\n\n"
        "Use of this type is *not recommended* as you lose the benefits of having a defined, static\n"
        "schema (one of the key benefits of GraphQL)."
    ),
    serialize=_serialize_json_string,
    parse_value=_parse_json_string,
)
