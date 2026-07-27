"""Domain/service layer for the organization app.

Three concerns live here, deliberately in separate modules:

``resource_access``
    The tenant-visibility helper and the cross-tenant resource-access resolver
    (ADR-0001). This module *is* the former ``organization/services.py``; every
    name it published is re-exported below so existing import statements are
    byte-identical.

``rolegrants``
    Principal-agnostic ``RoleGrant`` reconciliation, split into a read-only
    ``validate_grant_plan`` and a write-only ``sync_membership_grants`` that
    accepts only the token the validator produced.

``membership``
    Membership lifecycle — actor authorization, identity resolution, the
    ``Membership`` row, and grant orchestration.

``rolegrants`` and ``membership`` are intentionally NOT imported here.
``itambox.views.features`` imports this package at module scope purely for
``visible_to_containers``/``is_container_scoped_unfiltered``; re-exporting the
membership services would drag ``core.auth`` (which calls ``get_user_model()``
at import time) into that edge. Import them by their full path:

    from organization.services.membership import execute_membership_write
"""

from .resource_access import (
    DENIED_INSUFFICIENT_LEVEL,
    DENIED_NO_ACTIVE_TENANT,
    DENIED_NO_GRANT,
    DENIED_OWNER_UNRESOLVABLE,
    DENIED_RBAC,
    REASON_DIRECT_GRANT,
    REASON_GROUP_GRANT,
    REASON_SAME_TENANT,
    ResourceAccessDecision,
    is_container_scoped_unfiltered,
    resolve_stock_access,
    visible_to_containers,
)

__all__ = [
    "DENIED_INSUFFICIENT_LEVEL",
    "DENIED_NO_ACTIVE_TENANT",
    "DENIED_NO_GRANT",
    "DENIED_OWNER_UNRESOLVABLE",
    "DENIED_RBAC",
    "REASON_DIRECT_GRANT",
    "REASON_GROUP_GRANT",
    "REASON_SAME_TENANT",
    "ResourceAccessDecision",
    "is_container_scoped_unfiltered",
    "resolve_stock_access",
    "visible_to_containers",
]
