"""GraphQL transport scalars for the specification read contract.

The domain codecs remain the authority for field-specific validation.  These
scalars only enforce transport shape and the protocol-wide integer envelope;
in particular, Decimal is deliberately a string scalar so fixed scale is not
lost through a JSON number or a GraphQL Float.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import graphene
from graphql import GraphQLError
from graphql.language import ast

from extras.services.specifications.codecs import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN


def _scalar_error(name: str, value: object) -> GraphQLError:
    return GraphQLError(f"{name} cannot represent value: {value!r}")


def _safe_integer(value: object) -> int:
    if type(value) is not int:
        raise _scalar_error("SafeInteger", value)
    if not SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX:
        raise _scalar_error("SafeInteger", value)
    return value


class SafeInteger(graphene.Scalar):
    """An integer representable exactly by JavaScript clients."""

    class Meta:
        name = "SafeInteger"
        description = "An integer in the JavaScript safe-integer range."

    @staticmethod
    def serialize(value: object) -> int:
        return _safe_integer(value)

    @staticmethod
    def parse_value(value: object) -> int:
        return _safe_integer(value)

    @staticmethod
    def parse_literal(node: ast.ValueNode, _variables: Mapping[str, object] | None = None) -> int:
        if not isinstance(node, ast.IntValueNode):
            raise _scalar_error("SafeInteger", getattr(node, "value", node))
        return _safe_integer(int(node.value))


class DecimalScalar(graphene.Scalar):
    """A fixed-scale decimal transported as its exact string representation."""

    class Meta:
        name = "Decimal"
        description = "A decimal transported as a string to preserve fixed scale."

    @staticmethod
    def serialize(value: object) -> str:
        if type(value) is not str:
            raise _scalar_error("Decimal", value)
        return value

    @staticmethod
    def parse_value(value: object) -> str:
        if type(value) is not str:
            raise _scalar_error("Decimal", value)
        return value

    @staticmethod
    def parse_literal(node: ast.ValueNode, _variables: Mapping[str, object] | None = None) -> str:
        if not isinstance(node, ast.StringValueNode):
            raise _scalar_error("Decimal", getattr(node, "value", node))
        return node.value


class DateScalar(graphene.Scalar):
    """An ISO-8601 calendar date transported without a datetime coercion."""

    class Meta:
        name = "Date"
        description = "An ISO-8601 calendar date."

    @staticmethod
    def _parse(value: object) -> str:
        if type(value) is not str:
            raise _scalar_error("Date", value)
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise _scalar_error("Date", value) from exc
        return value

    @staticmethod
    def serialize(value: object) -> str:
        if isinstance(value, date):
            return value.isoformat()
        return DateScalar._parse(value)

    @staticmethod
    def parse_value(value: object) -> str:
        return DateScalar._parse(value)

    @staticmethod
    def parse_literal(node: ast.ValueNode, _variables: Mapping[str, object] | None = None) -> str:
        if not isinstance(node, ast.StringValueNode):
            raise _scalar_error("Date", getattr(node, "value", node))
        return DateScalar._parse(node.value)


class CursorScalar(graphene.Scalar):
    """Opaque, non-empty cursor text used by bounded connections."""

    class Meta:
        name = "Cursor"

    @staticmethod
    def serialize(value: object) -> str:
        if type(value) is not str or not value:
            raise _scalar_error("Cursor", value)
        return value

    @staticmethod
    def parse_value(value: object) -> str:
        if type(value) is not str or not value:
            raise _scalar_error("Cursor", value)
        return value

    @staticmethod
    def parse_literal(node: ast.ValueNode, _variables: Mapping[str, object] | None = None) -> str:
        if not isinstance(node, ast.StringValueNode):
            raise _scalar_error("Cursor", getattr(node, "value", node))
        return CursorScalar.parse_value(node.value)


def _json_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool, float}:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    raise _scalar_error("JSON", value)


class JSONScalar(graphene.Scalar):
    """Read-only JSON value transport for uninterpreted history."""

    class Meta:
        name = "JSON"

    @staticmethod
    def serialize(value: object) -> object:
        return _json_value(value)

    @staticmethod
    def parse_value(value: object) -> object:
        return _json_value(value)

    @staticmethod
    def parse_literal(node: ast.ValueNode, variables: Mapping[str, object] | None = None) -> object:
        del variables
        if isinstance(node, ast.NullValueNode):
            return None
        if isinstance(node, ast.StringValueNode):
            return node.value
        if isinstance(node, ast.IntValueNode):
            return int(node.value)
        if isinstance(node, ast.FloatValueNode):
            return float(node.value)
        if isinstance(node, ast.BooleanValueNode):
            return node.value
        if isinstance(node, ast.ListValueNode):
            return [JSONScalar.parse_literal(item) for item in node.values]
        if isinstance(node, ast.ObjectValueNode):
            return {field.name.value: JSONScalar.parse_literal(field.value) for field in node.fields}
        raise _scalar_error("JSON", node)


__all__ = [
    "CursorScalar",
    "DateScalar",
    "DecimalScalar",
    "JSONScalar",
    "SAFE_INTEGER_MAX",
    "SAFE_INTEGER_MIN",
    "SafeInteger",
]
