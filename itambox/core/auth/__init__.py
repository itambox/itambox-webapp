import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from core.managers import (
    get_current_tenant,
    get_current_tenant_group,
    get_current_membership,
    set_current_tenant,
    set_current_membership,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class PasswordLoginOnlyBackend(ModelBackend):
    """Delegates ``authenticate(username/password)`` to ModelBackend but rejects all
    permission checks so the only path that grants permissions is the unified
    :class:`MembershipBackend` below.
    """
    def user_can_authenticate(self, user):
        # Standard ModelBackend hook (also checks ``is_active``). A user with
        # ``can_login=False`` may never perform interactive password login — independent of
        # ``is_active`` (account status) and of API-token access. ``getattr`` keeps this safe
        # if the user model lacks the field (e.g. during a partial migration).
        return super().user_can_authenticate(user) and getattr(user, 'can_login', True)

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


class MembershipBackend:
    """Unified RBAC backend — one container type (Tenant), one vocabulary.

    ``user.has_perm(perm, obj=...)`` is the only authorization currency. A user's
    permissions inside a tenant ``T`` are the additive union of:

      1. own-reach :class:`RoleAssignment` rows on their active ``Membership(T)``;
      2. roles owned by ``T`` carried by an active ``UserGroup`` they belong to;
      3. managed-reach assignments on their active membership at ``T.managed_by``
         (the managing/provider tenant) whose refinement covers ``T``.

    There is no second resolution path: administering a provider tenant is simply
    holding permissions inside THAT tenant (rule 1/2 there), and nothing is
    stripped in the managed projection — role content alone decides what a grant
    conveys, and the escalation guard decides who may create it.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        return None

    # ------------------------------------------------------------------ tenant perms
    def _effective_perms_for_tenant(self, user_obj, tenant):
        """Frozen union of permissions the user holds inside ``tenant``.

        Cached on the user as ``_perms_tenant_<pk>`` so the dozens of ``has_perm`` checks
        in a single request cost at most three queries.
        """
        cache_key = f'_perms_tenant_{tenant.pk}'
        if hasattr(user_obj, cache_key):
            return getattr(user_obj, cache_key)

        # inline import: avoids AppRegistryNotReady at auth-backend import time.
        from organization.rbac import resolve_effective_permissions

        result = resolve_effective_permissions(user_obj, tenant)
        setattr(user_obj, cache_key, result)
        return result

    # ------------------------------------------------------------------ context resolution
    def _object_tenant(self, user_obj, obj):
        """The tenant carried by ``obj`` (a Tenant instance IS its own context), or
        ``None`` for a tenant-less (global/shared) object."""
        from organization.models import Tenant, Membership

        obj_tenant = getattr(obj, 'tenant', None)
        if obj_tenant is None and isinstance(obj, Tenant):
            obj_tenant = obj
        if obj_tenant is not None:
            # Pre-cache the (possibly None) active tenant membership for later calls.
            mem_cache_key = f'_tenant_membership_{obj_tenant.pk}'
            if not hasattr(user_obj, mem_cache_key):
                membership = Membership.objects.filter(
                    user=user_obj, tenant=obj_tenant, is_active=True,
                ).first()
                setattr(user_obj, mem_cache_key, membership)
        return obj_tenant

    def _group_scope_tenants(self, user_obj):
        """Accessible tenants inside the active tenant-group scope, or ``None``
        when no group scope applies (single active tenant, or no scope at all).

        Mirrors ``TenantScopingQuerySet.filter_by_tenant``'s member branch —
        the canonical accessible set (memberships, UserGroup grants, managed
        reach) intersected with the scoped group's subtree — so the ambient
        permission gate agrees with what the scoped querysets will show.
        Cached on the user per group for the request's many ``has_perm`` calls.
        """
        if get_current_tenant() is not None:
            return None
        group = get_current_tenant_group()
        if group is None:
            return None
        cache_key = f'_group_scope_tenants_{group.pk}'
        if hasattr(user_obj, cache_key):
            return getattr(user_obj, cache_key)
        # inline imports: avoid AppRegistryNotReady / a core<->organization cycle at load
        from organization.models import Tenant
        from organization.access import (
            accessible_tenant_ids, get_descendant_tenant_group_ids,
        )
        # live_only: prune soft-deleted subgroups exactly like filter_by_tenant's
        # walk, so the gate never counts a tenant the scoped querysets will hide.
        tenants = list(Tenant._base_manager.filter(
            pk__in=accessible_tenant_ids(user_obj),
            group_id__in=get_descendant_tenant_group_ids(group.pk, live_only=True),
            deleted_at__isnull=True,
        ))
        setattr(user_obj, cache_key, tenants)
        return tenants

    def _ambient_tenant(self, user_obj):
        """Single-tenant ambient context: bound membership → current tenant →
        first active membership's tenant → first managed-reachable tenant."""
        from organization.models import Tenant, Membership

        membership = get_current_membership()
        if membership and membership.user_id == user_obj.pk and membership.tenant_id:
            return membership.tenant
        current_tenant = get_current_tenant()
        if current_tenant is not None:
            return current_tenant
        first = Membership.objects.filter(
            user=user_obj, is_active=True,
        ).select_related('tenant').first()
        if first is not None:
            set_current_tenant(first.tenant)
            set_current_membership(first)
            return first.tenant
        # Defensive fallback: a user whose only access is managed reach (should not
        # happen — reach rides on a membership — but fail towards a valid context).
        from organization.access import managed_accessible_tenant_ids
        reachable = managed_accessible_tenant_ids(user_obj)
        if reachable:
            tenant = Tenant._base_manager.filter(pk__in=reachable).order_by('name').first()
            if tenant is not None:
                set_current_tenant(tenant)
                return tenant
        return None

    def _resolve_tenant(self, user_obj, obj):
        """Resolve the single-tenant context to evaluate against, or ``None``.

        Objects resolve through their ``tenant`` attribute; a tenant-less object
        falls through to the ambient chain. Group-scoped ambient checks never get
        here — ``has_perm``/``has_module_perms`` branch to the group union first.
        """
        if obj is not None:
            obj_tenant = self._object_tenant(user_obj, obj)
            if obj_tenant is not None:
                return obj_tenant
        return self._ambient_tenant(user_obj)

    # ------------------------------------------------------------------ public API
    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        tenant = self._object_tenant(user_obj, obj) if obj is not None else None
        if tenant is None:
            # Ambient check (list/add gates, nav). Under an active tenant-group
            # scope the page aggregates every accessible tenant in the subtree,
            # so the gate passes when the permission is held in ANY of them and
            # fails closed when it is held in none. Anchoring at the user's
            # first membership instead would 403 a managed-only user whose home
            # (provider) membership carries no own-reach roles — and the
            # first-membership fallback would stomp the group context mid-request.
            group_tenants = self._group_scope_tenants(user_obj)
            if group_tenants is not None:
                return any(
                    perm in self._effective_perms_for_tenant(user_obj, tenant)
                    for tenant in group_tenants
                )
            tenant = self._ambient_tenant(user_obj)
        if tenant is None:
            return False
        return perm in self._effective_perms_for_tenant(user_obj, tenant)

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        prefix = app_label + '.'
        # Same group-union semantics as the ambient has_perm gate above.
        group_tenants = self._group_scope_tenants(user_obj)
        if group_tenants is not None:
            return any(
                p.startswith(prefix)
                for tenant in group_tenants
                for p in self._effective_perms_for_tenant(user_obj, tenant)
            )
        tenant = self._ambient_tenant(user_obj)
        if tenant is None:
            return False
        return any(p.startswith(prefix) for p in self._effective_perms_for_tenant(user_obj, tenant))


# Backwards-compat alias: existing settings reference TenantMembershipBackend.
TenantMembershipBackend = MembershipBackend
