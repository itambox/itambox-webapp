import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from core.managers import (
    get_current_tenant,
    get_current_membership,
    set_current_tenant,
    set_current_membership,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class PasswordLoginOnlyBackend(ModelBackend):
    """
    A custom authentication backend that delegates authentication (username/password validation)
    to ModelBackend but rejects all permissions checking (has_perm/has_module_perms) to prevent
    bypassing the custom multi-tenant RBAC system.
    """
    def has_perm(self, user_obj, perm, obj=None):
        return False

    def has_module_perms(self, user_obj, app_label):
        return False

    def get_all_permissions(self, user_obj, obj=None):
        return set()

    def get_user_permissions(self, user_obj, obj=None):
        return set()

    def get_group_permissions(self, user_obj, obj=None):
        return set()


class TenantMembershipBackend:
    """
    Resolves permissions dynamically at request time based on the active membership.

    A user's effective permissions in a tenant are the ADDITIVE UNION of:
      1. ``TenantMembership.direct_permissions`` (per-membership JSON grants),
      2. the permissions of every ``TenantRole`` attached to the membership, and
      3. the permissions of every ``TenantRole`` carried by an active ``UserGroup``
         the user belongs to IN THAT TENANT.

    The union is gated on an *active* membership in the tenant: a non-member or a
    suspended (``is_active=False``) membership grants nothing, even via groups.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        return None

    def _effective_perms(self, user_obj, tenant):
        """Return a cached ``frozenset`` of the user's effective permissions in ``tenant``.

        Cached on the user object per tenant (``_effective_perms_<pk>``) so the 40+
        ``has_perm`` checks in a single request resolve with at most two queries.

        Resolution is INDEPENDENT of the current-tenant contextvar: roles are read via
        ``TenantRole._base_manager`` (the unscoped base manager) with an explicit
        ``deleted_at__isnull=True`` filter. The tenant-scoping default manager
        (``TenantScopingSoftDeleteManager``) filters ``roles.all()`` to whatever tenant
        is *currently active* — and fails closed to nothing when none is set — which
        would silently drop a multi-tenant user's roles when checking an object in a
        tenant other than the active one. Soft-deleted roles must also never grant.
        """
        cache_key = f'_effective_perms_{tenant.pk}'
        if hasattr(user_obj, cache_key):
            return getattr(user_obj, cache_key)

        # inline import: avoids AppRegistryNotReady at module load
        from organization.models import TenantMembership, TenantRole

        mem_cache_key = f'_tenant_membership_{tenant.pk}'
        if hasattr(user_obj, mem_cache_key):
            membership = getattr(user_obj, mem_cache_key)
        else:
            membership = TenantMembership.objects.filter(
                user=user_obj, tenant=tenant, is_active=True,
            ).first()
            # Preserve the membership-cache contract other code reads directly.
            setattr(user_obj, mem_cache_key, membership)

        perms = set()
        # (1)+(2) The user's own active membership in this tenant (if any): direct
        # permission grants plus the permissions of every attached role. _base_manager
        # is unscoped (no tenant/soft-delete filtering), so deleted_at is explicit.
        if membership is not None:
            perms.update(membership.direct_permissions or [])
            for perm_list in TenantRole._base_manager.filter(
                memberships=membership, deleted_at__isnull=True,
            ).values_list('permissions', flat=True):
                perms.update(perm_list or [])

        # (3) Cross-tenant user groups: any active group the user belongs to that
        # carries a (non-deleted) role IN THIS tenant grants that role's permissions —
        # independent of whether the user has a TenantMembership here. This is what
        # lets a global MSP team grant access across customer tenants. Groups are
        # global (no tenant of their own); the ROLE carries the tenant.
        for perm_list in TenantRole._base_manager.filter(
            tenant=tenant, deleted_at__isnull=True,
            user_groups__members=user_obj,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('permissions', flat=True):
            perms.update(perm_list or [])

        # (4) Provider grants (MSP layer): if this tenant is managed by a provider and the
        # user is active provider staff for it, add their ProviderRole's tenant-role-template
        # permissions — but ONLY when the tenant is within the membership's tenant_scope.
        # This is additive with the membership/group grants above. No provider → skipped, so
        # single-company installs are unaffected.
        if getattr(tenant, 'provider_id', None):
            # inline import: avoids AppRegistryNotReady at module load
            from users.models import ProviderMembership
            pm = ProviderMembership.objects.filter(
                user=user_obj, provider_id=tenant.provider_id, is_active=True,
            ).select_related('provider_role__tenant_role_template').first()
            if pm and pm.provider_role and pm.provider_role.tenant_role_template:
                if self._tenant_in_scope(pm, tenant):
                    perms.update(pm.provider_role.tenant_role_template.permissions or [])

        result = frozenset(perms)
        setattr(user_obj, cache_key, result)
        return result

    def _tenant_in_scope(self, pm, tenant):
        """Whether ``tenant`` falls within ProviderMembership ``pm``'s tenant_scope.

        ``all`` → any tenant of the provider; ``tenant_group`` → tenant's group is the
        scope group or a descendant; ``explicit`` → tenant is in ``assigned_tenants``.
        The whole ``_effective_perms`` result is cached per (user, tenant), so this runs
        at most once per tenant per request.
        """
        # inline import: avoids AppRegistryNotReady at module load
        from users.models import ProviderMembership
        if pm.tenant_scope == ProviderMembership.SCOPE_ALL:
            return True
        if pm.tenant_scope == ProviderMembership.SCOPE_TENANT_GROUP:
            if not pm.scope_group_id or not tenant.group_id:
                return False
            from organization.access import get_descendant_tenant_group_ids
            return tenant.group_id in get_descendant_tenant_group_ids(pm.scope_group_id)
        # SCOPE_EXPLICIT (default, least privilege)
        return pm.assigned_tenants.filter(pk=tenant.pk).exists()

    def _resolve_tenant(self, user_obj, obj):
        """Resolve the tenant whose permissions apply for this check, or ``None``.

        obj-path: the object's tenant, but only if the user has an ACTIVE membership
        there (strict tenant boundary — a non-member is denied regardless of any
        group/AssetHolder). This path does NOT mutate the global tenant contextvar.

        no-obj path: the current membership, else the user's first active membership —
        and on that fallback the historical ``set_current_tenant``/``set_current_membership``
        side-effect is preserved (Django admin / middleware rely on it).
        """
        # inline import: avoids AppRegistryNotReady at module load
        from organization.models import TenantMembership

        if obj is not None:
            obj_tenant = getattr(obj, 'tenant', None)
            # If the object itself is a Tenant
            if obj_tenant is None and obj.__class__.__name__.lower() == 'tenant':
                obj_tenant = obj
            if obj_tenant is not None:
                # Cache the (possibly None) active membership; access to obj_tenant is
                # decided by _effective_perms, which also grants via cross-tenant group
                # roles. A user with neither a membership nor a group role in obj_tenant
                # resolves to an empty perm set => denied (strict boundary preserved).
                cache_key = f'_tenant_membership_{obj_tenant.pk}'
                if not hasattr(user_obj, cache_key):
                    membership = TenantMembership.objects.filter(
                        user=user_obj, tenant=obj_tenant, is_active=True,
                    ).first()
                    setattr(user_obj, cache_key, membership)
                return obj_tenant
            # obj has no tenant attribute -> fall through to ambient resolution.

        membership = get_current_membership()
        if membership and membership.user_id != user_obj.pk:
            membership = None
        if membership:
            return membership.tenant
        # No bound membership. If a tenant is explicitly active, resolve in THAT
        # tenant (gating on membership there) instead of leaking perms from an
        # unrelated tenant the user happens to belong to. _effective_perms returns
        # an empty set when the user has no active membership in this tenant.
        current_tenant = get_current_tenant()
        if current_tenant is not None:
            return current_tenant
        # No tenant context at all (e.g. a background task): fall back to the user's
        # first active membership and bind it for the remainder of the request.
        membership = TenantMembership.objects.filter(
            user=user_obj, is_active=True,
        ).select_related('tenant').first()
        if membership:
            set_current_tenant(membership.tenant)
            set_current_membership(membership)
            return membership.tenant
        # No TenantMembership at all. Provider staff (no direct membership) resolve to the
        # first tenant reachable via their ProviderMembership scope, so ambient (no-obj)
        # permission checks work for MSP operators. Perms in that tenant still come from
        # _effective_perms (provider-grant branch).
        from organization.access import provider_accessible_tenant_ids
        provider_ids = provider_accessible_tenant_ids(user_obj)
        if provider_ids:
            from organization.models import Tenant
            tenant = Tenant._base_manager.filter(
                pk__in=provider_ids,
            ).order_by('name').first()
            if tenant is not None:
                set_current_tenant(tenant)
                return tenant
        # No membership and no provider access: deny rather than falling back to
        # ModelBackend (which would grant model-level perms to non-members).
        return None

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False
        # Superusers bypass scoping entirely
        if user_obj.is_superuser:
            return True
        tenant = self._resolve_tenant(user_obj, obj)
        if tenant is None:
            return False
        return perm in self._effective_perms(user_obj, tenant)

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        tenant = self._resolve_tenant(user_obj, None)
        if tenant is None:
            return False
        prefix = app_label + '.'
        return any(p.startswith(prefix) for p in self._effective_perms(user_obj, tenant))

# GlobalCapabilityBackend was removed in the MSP-RBAC redesign: global/provider-level
# capabilities (managing groups, tenants, provider users) are now carried by ProviderRole
# booleans and checked directly via core.auth.provider.has_provider_capability /
# can_manage_user_groups, rather than smuggled through user_permissions + a separate
# auth backend with a hardcoded whitelist.
