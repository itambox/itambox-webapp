"""Shared persistence service for custom-field value maps."""

from __future__ import annotations

import contextvars
import math
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import Iterator, Protocol, TypeVar, cast

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, transaction

from core.context import _current_user, _request_id
from itambox.registry import registry


class _ModelState(Protocol):
    db: str | None


class CustomFieldDataOwner(Protocol):
    custom_field_data: object
    _state: _ModelState

    def save(self, *, using: str | None = None, update_fields: Sequence[str] | None = None) -> None:
        pass


OwnerT = TypeVar("OwnerT", bound=CustomFieldDataOwner)


def _json_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        raise ValidationError("Custom-field data must contain finite JSON numbers.")
    if isinstance(value, Mapping):
        return _json_object(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValidationError("Custom-field data must contain JSON values.")


def _json_object(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if type(key) is not str:
            raise ValidationError("Custom-field data keys must be strings.")
        result[key] = _json_value(value)
    return result


@contextmanager
def actor_change_context(actor: object) -> Iterator[None]:
    """Attribute a value write through the existing ChangeLoggingMixin context."""
    user_context = cast(contextvars.ContextVar[object | None], _current_user)
    request_context = cast(contextvars.ContextVar[uuid.UUID | None], _request_id)
    user_token = user_context.set(actor)
    request_token = None
    if not request_context.get():
        request_token = request_context.set(uuid.uuid4())
    try:
        yield
    finally:
        if request_token is not None:
            request_context.reset(request_token)
        user_context.reset(user_token)


def write_custom_field_data(
    owner: OwnerT,
    values: Mapping[str, object],
    *,
    actor: object | None = None,
    update_fields: Sequence[str] | None = None,
    using: str | None = None,
    commit: bool = True,
) -> OwnerT:
    """Replace one owner's value map and optionally persist it in a savepoint.

    ``values`` is recursively detached into JSON-compatible dictionaries/lists,
    so immutable DTO mappings and tuple-valued multi-selects become ordinary
    JSON data before model validation and persistence.
    """
    if not isinstance(values, Mapping):
        raise ValidationError("Custom-field data must be an object.")
    normalized_values = _json_object(values)
    previous_values = owner.custom_field_data
    owner.custom_field_data = normalized_values
    validator = registry.get_custom_field_data_validator(type(owner))
    try:
        if validator is not None:
            validator(owner)
    except Exception:  # broad except: cleanup-reraise: restore the owner's prior map before propagating failure.
        owner.custom_field_data = previous_values
        raise
    if not commit:
        return owner

    database = using or getattr(getattr(owner, "_state", None), "db", None) or DEFAULT_DB_ALIAS
    fields = None if update_fields is None else list(dict.fromkeys((*update_fields, "custom_field_data")))
    with transaction.atomic(using=database):
        if actor is None:
            owner.save(using=database, update_fields=fields)
        else:
            with actor_change_context(actor):
                owner.save(using=database, update_fields=fields)
    return owner
