"""Bounded JSON parsing with duplicate-key and scalar safety checks."""

from __future__ import annotations

import json
import math
from typing import Any

from .errors import issue
from .limits import ValidationLimits


class _DuplicateProperty(Exception):
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


class _NonFiniteNumber(Exception):
    __slots__ = ("literal",)

    def __init__(self, literal: str):
        self.literal = literal


def _object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _DuplicateProperty(name)
        result[name] = value
    return result


def _reject_non_finite_number(literal: str) -> Any:
    raise _NonFiniteNumber(literal)


def _as_utf8_bytes(document: bytes | str, max_bytes: int) -> tuple[bytes, str]:
    if isinstance(document, (bytes, str)) and len(document) > max_bytes:
        raise issue("RESOURCE_LIMIT", (), f"The UTF-8 document exceeds the {max_bytes}-byte limit")
    if isinstance(document, bytes):
        raw = document
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise issue("INVALID_UTF8", (), "The document must be valid UTF-8 JSON") from exc
    elif isinstance(document, str):
        try:
            raw = document.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise issue("INVALID_UNICODE", (), "The document contains a lone Unicode surrogate") from exc
        text = document
    else:
        raise TypeError("A library document must be UTF-8 bytes or a string")
    if len(raw) > max_bytes:
        raise issue("RESOURCE_LIMIT", (), f"The UTF-8 document exceeds the {max_bytes}-byte limit")
    return raw, text


def _walk_json_tree(value: Any, *, depth: int, limits: ValidationLimits, path: tuple[str | int, ...]) -> None:
    if depth > limits.max_depth:
        raise issue("RESOURCE_LIMIT", path, f"JSON nesting exceeds the depth limit of {limits.max_depth}")
    if isinstance(value, dict):
        for key, child in value.items():
            if any(0xD800 <= ord(character) <= 0xDFFF for character in key):
                raise issue("INVALID_UNICODE", path + (key,), "Object keys may not contain lone surrogates")
            _walk_json_tree(child, depth=depth + 1, limits=limits, path=path + (key,))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_json_tree(child, depth=depth + 1, limits=limits, path=path + (index,))
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise issue("INVALID_UNICODE", path, "Strings may not contain lone surrogates")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise issue("INVALID_NUMBER", path, "JSON numbers must be finite")


def parse_json_document(document: bytes | str, *, limits: ValidationLimits) -> dict[str, Any]:
    """Parse one bounded JSON object without ever overwriting a duplicate key."""

    _raw, text = _as_utf8_bytes(document, limits.max_bytes)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_pairs_without_duplicates,
            parse_constant=_reject_non_finite_number,
        )
    except _DuplicateProperty as exc:
        raise issue("DUPLICATE_PROPERTY", (), f"JSON property {exc.name!r} occurs more than once") from exc
    except _NonFiniteNumber as exc:
        raise issue("INVALID_NUMBER", (), f"JSON number {exc.literal!r} is not finite") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise issue("INVALID_JSON", (), "The document is not valid JSON") from exc
    except ValueError as exc:
        # Python can reject an otherwise syntactically valid integer before the
        # bounded semantic pass when it exceeds the interpreter's digit guard.
        raise issue("INVALID_NUMBER", (), "JSON number exceeds the parser's integer limit") from exc
    if not isinstance(parsed, dict):
        raise issue("SCHEMA_TYPE", (), "The document root must be a JSON object")
    _walk_json_tree(parsed, depth=1, limits=limits, path=())
    return parsed


__all__ = ["parse_json_document"]
