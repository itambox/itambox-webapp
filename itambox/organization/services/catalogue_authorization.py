"""Authorization for catalogue mutations whose effects are global."""

from __future__ import annotations

from core.context import get_current_all_accessible, get_current_tenant, get_current_tenant_group


def has_provider_catalogue_permission(actor: object, permission: str) -> bool:
    """Check a role-aware permission against the active provider tenant only.

    Catalogue rows are global, so ordinary object-permission checks have no
    tenant anchor of their own. Requiring the concrete provider tenant here
    prevents a permission inherited from an unrelated customer, tenant-group,
    or All-accessible scope from authorizing a global catalogue operation.
    Superusers retain their platform-level override.
    """
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        return False
    if getattr(actor, "is_superuser", False):
        return True
    if not isinstance(permission, str) or permission.count(".") != 1:
        return False
    app_label, codename = permission.split(".", 1)
    if not app_label or not codename:
        return False

    # An object anchor must not override an active aggregate selection. The RBAC
    # backend accepts the explicit tenant object even when group/all-accessible
    # context is present, so fail closed before asking it for the object grant.
    if get_current_tenant_group() is not None or get_current_all_accessible():
        return False

    provider_tenant = get_current_tenant()
    if (
        provider_tenant is None
        or not getattr(provider_tenant, "is_provider", False)
        or getattr(provider_tenant, "deleted_at", None) is not None
    ):
        return False

    check_permission = getattr(actor, "has_perm", None)
    return bool(check_permission and check_permission(permission, obj=provider_tenant))
