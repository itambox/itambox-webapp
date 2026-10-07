"""GraphQL input objects retained by the read-only scope query surface."""

from __future__ import annotations

import graphene

from .types import ScopeModeEnum


class RequestedScopeSelectorInput(graphene.InputObjectType):
    class Meta:
        name = "RequestedScopeSelector"

    mode = ScopeModeEnum(required=True)
    tenant_id = graphene.ID()
    tenant_group_id = graphene.ID()


__all__ = ["RequestedScopeSelectorInput"]
