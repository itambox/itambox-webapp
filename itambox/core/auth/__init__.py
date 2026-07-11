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

        # inline imports: avoid AppRegistryNotReady at module load
        from organization.models import Role, RoleAssignment

        perms = set()

        # (1) Own-reach assignments on the user's active membership in this tenant.
        for perm_list in Role._base_manager.filter(
            deleted_at__isnull=True,
            assignments__reach=RoleAssignment.REACH_OWN,
            assignments__membership__user=user_obj,
            assignments__membership__tenant=tenant,
            assignments__membership__is_active=True,
        ).values_list('permissions', flat=True):
            perms.update(perm_list or [])

        # (2) Cross-tenant UserGroups: any active group the user belongs to whose attached
        # roles are owned by THIS tenant. _base_manager keeps the lookup context-independent.
        for perm_list in Role._base_manager.filter(
            tenant=tenant, deleted_at__isnull=True,
            user_groups__members=user_obj,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('permissions', flat=True):
            perms.update(perm_list or [])

        # (3) Managed-reach projection from the managing tenant (single hop, depth 1).
        if tenant.managed_by_id:
            for assignment in RoleAssignment.objects.filter(
                reach=RoleAssignment.REACH_MANAGED,
                membership__user=user_obj,
                membership__tenant_id=tenant.managed_by_id,
                membership__is_active=True,
            ).select_related('role', 'scope_group', 'membership'):
                if assignment.role.deleted_at is None and assignment.covers_tenant(tenant):
                    perms.update(assignment.role.permissions or [])

        result = frozenset(perms)
        setattr(user_obj, cache_key, result)
        return result

    # ------------------------------------------------------------------ context resolution
    def _resolve_tenant(self, user_obj, obj):
        """Resolve the tenant context to evaluate against, or ``None``.

        Objects resolve through their ``tenant`` attribute (a Tenant instance IS its own
        context). With no object, ambient state applies: bound membership → current
        tenant → first active membership's tenant → first managed-reachable tenant.
        """
        from organization.models import Tenant, Membership

        if obj is not None:
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
            # Tenant-less object (global/shared) — fall through to ambient resolution.

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

    # ------------------------------------------------------------------ public API
    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        tenant = self._resolve_tenant(user_obj, obj)
        if tenant is None:
            return False
        return perm in self._effective_perms_for_tenant(user_obj, tenant)

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        tenant = self._resolve_tenant(user_obj, None)
        if tenant is None:
            return False
        prefix = app_label + '.'
        return any(p.startswith(prefix) for p in self._effective_perms_for_tenant(user_obj, tenant))


# Backwards-compat alias: existing settings reference TenantMembershipBackend.
TenantMembershipBackend = MembershipBackend
