"""RFC 8785 canonical JSON bytes used by specification-library releases."""

from __future__ import annotations

import rfc8785


def canonicalize_release_document(source_document: dict) -> bytes:
    """Return RFC 8785 JCS bytes for an already domain-normalized object.

    JCS canonicalizes JSON bytes only. It recursively orders object members and
    formats JSON numbers, but it deliberately preserves array order and does
    not apply ITAMbox domain normalization (identity sorting, decimal scale,
    or semantic value ordering).
    """
    if not isinstance(source_document, dict):
        raise TypeError("A release document must be a JSON object")
    return rfc8785.dumps(source_document)
