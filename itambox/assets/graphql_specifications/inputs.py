"""GraphQL input objects retained by the read-only scope query surface."""

from __future__ import annotations

import strawberry

from .types import ScopeModeEnum


@strawberry.input(name="RequestedScopeSelector")
class RequestedScopeSelectorInput:
    mode: ScopeModeEnum
    tenant_id: strawberry.ID | None = None
    tenant_group_id: strawberry.ID | None = None


__all__ = ["RequestedScopeSelectorInput"]
