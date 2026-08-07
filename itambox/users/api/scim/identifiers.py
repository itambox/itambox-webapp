"""Identifier resolution for the SCIM 1.x dual-read compatibility window."""

from typing import TypeVar
from uuid import UUID

from django.db.models import Model, QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404

MAX_LEGACY_PK = 2**63 - 1
_ScimModelT = TypeVar("_ScimModelT", bound=Model)


def identifier_lookup(identifier: int | str) -> dict[str, int | UUID]:
    """Return the model lookup for a legacy integer or opaque SCIM identifier.

    Decimal path segments remain read-compatible throughout 1.x. Every other
    identifier must be a valid UUID; malformed values fail closed rather than
    falling through to an unscoped primary-key lookup.
    """
    value = str(identifier)
    if value.isascii() and value.isdecimal():
        if len(value) > 19 or int(value) > MAX_LEGACY_PK:
            raise Http404("Invalid SCIM resource identifier.")
        return {"pk": int(value)}
    try:
        return {"scim_id": UUID(value)}
    except (TypeError, ValueError, AttributeError) as exc:
        raise Http404("Invalid SCIM resource identifier.") from exc


def get_scim_object_or_404(queryset: QuerySet[_ScimModelT], identifier: int | str) -> _ScimModelT:
    """Resolve a SCIM detail identifier while preserving queryset scoping."""
    return get_object_or_404(queryset, **identifier_lookup(identifier))


def identifier_lookup_or_none(identifier: int | str) -> dict[str, int | UUID] | None:
    """Return a dual-read lookup or ``None`` for an invalid member identifier."""
    try:
        return identifier_lookup(identifier)
    except Http404:
        return None
