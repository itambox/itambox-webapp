from rest_framework.exceptions import MethodNotAllowed
from rest_framework.permissions import SAFE_METHODS

from core.managers import (
    get_current_all_accessible,
    get_current_scope_conflict,
    get_current_tenant,
    get_current_tenant_group,
)
from itambox.api.permissions import TokenPermissions
from organization.services.resource_access import _resource_grant_container_ids, visible_to_containers


class TenantResourceGrantAuditPermission(TokenPermissions):
    """Read permission plus the grant-specific container boundary."""

    permission = "organization.view_tenantresourcegrant"

    def _scope_is_bound(self, request):
        if get_current_scope_conflict(request.user):
            return False
        token_tenant_id = getattr(getattr(request, "auth", None), "tenant_id", None)
        tenant = get_current_tenant()
        if token_tenant_id is not None:
            return (
                tenant is not None
                and tenant.pk == token_tenant_id
                and getattr(request.auth, "user_id", None) == request.user.pk
            )
        if request.user.is_superuser:
            return True
        return bool(tenant or get_current_tenant_group() or get_current_all_accessible())

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not self._scope_is_bound(request):
            return False
        if not request.user.has_perms((self.permission,)):
            return False
        if request.method not in SAFE_METHODS:
            raise MethodNotAllowed(request.method)
        container_ids = _resource_grant_container_ids(request.user, self.permission, request=request)
        return container_ids is None or bool(container_ids)

    def has_object_permission(self, request, view, obj):
        if request.method not in SAFE_METHODS:
            return False
        return visible_to_containers(
            request.user,
            view.get_queryset().filter(pk=obj.pk),
            self.permission,
            request=request,
        ).exists()
