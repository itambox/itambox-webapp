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
    """Unified RBAC backend.

    Resolves a user's permissions through a single ``Membership`` model that may bind
    them to either a ``Tenant`` (member) or a ``Provider`` (staff). All
    permissions — including provider-level capabilities such as
    ``organization.manage_tenants`` — flow through this one backend, so
    ``user.has_perm(perm, obj=...)`` is the only authorization currency.

    Resolution rules:

      * ``has_perm(user, perm, obj=Provider)`` or ``obj`` with a ``provider`` attr →
        resolve against the user's active provider Membership for that Provider.

      * ``has_perm(user, perm, obj=tenant_obj)`` → resolve against the user's permissions
        in that tenant, which is the additive union of:

          1. direct tenant Membership ``direct_permissions`` + attached tenant-scoped Role permissions;
          2. tenant-scoped Roles carried by an active ``UserGroup`` the user belongs to,
             whose ``role.tenant`` equals this tenant;
          3. provider-scoped Roles attached to the user's provider Membership for
             ``tenant.provider`` (when that tenant is within ``tenant_scope``).

      * ``has_perm(user, perm)`` with no obj → use the current bound membership / tenant,
        else first accessible tenant or provider.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        return None

    # ------------------------------------------------------------------ tenant perms
    def _effective_perms_for_tenant(self, user_obj, tenant):
        """Frozen union of permissions the user holds inside ``tenant``.

        Cached on the user as ``_perms_tenant_<pk>`` so the dozens of ``has_perm`` checks
        in a single request cost at most two queries.
        """
        cache_key = f'_perms_tenant_{tenant.pk}'
        if hasattr(user_obj, cache_key):
            return getattr(user_obj, cache_key)

        # inline imports: avoid AppRegistryNotReady at module load
        from organization.models import Membership, Role

        perms = set()

        # (1) Direct tenant membership: direct grants + attached role permissions.
        mem_cache_key = f'_tenant_membership_{tenant.pk}'
        if hasattr(user_obj, mem_cache_key):
            membership = getattr(user_obj, mem_cache_key)
        else:
            membership = Membership.objects.filter(
                user=user_obj, tenant=tenant, is_active=True,
            ).first()
            setattr(user_obj, mem_cache_key, membership)

        if membership is not None:
            perms.update(membership.direct_permissions or [])
            for perm_list in Role._base_manager.filter(
                memberships=membership, deleted_at__isnull=True, scope=Role.SCOPE_TENANT,
            ).values_list('permissions', flat=True):
                perms.update(perm_list or [])

        # (2) Cross-tenant UserGroups: any active group the user belongs to whose attached
        # roles target THIS tenant. _base_manager keeps the lookup tenant-context-independent.
        for perm_list in Role._base_manager.filter(
            scope=Role.SCOPE_TENANT, tenant=tenant, deleted_at__isnull=True,
            user_groups__members=user_obj,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('permissions', flat=True):
            perms.update(perm_list or [])

        # (3) Provider staff projection: if this tenant has a provider and the user is
        # active staff there whose tenant_scope covers this tenant, every provider-scoped
        # Role attached to that staff membership contributes its permissions.
        if getattr(tenant, 'provider_id', None):
            staff = Membership.objects.filter(
                user=user_obj, provider_id=tenant.provider_id, is_active=True,
            ).select_related('scope_group').first()
            if staff is not None and staff.covers_tenant(tenant):
                for perm_list in Role._base_manager.filter(
                    memberships=staff, deleted_at__isnull=True, scope=Role.SCOPE_PROVIDER,
                ).values_list('permissions', flat=True):
                    # Strip organization.manage_* in the projection via the canonical helper
                    # (Membership.project_permissions_for_tenant is the single source of truth).
                    perms.update(Membership.project_permissions_for_tenant(perm_list))

        result = frozenset(perms)
        setattr(user_obj, cache_key, result)
        return result

    # ----------------------------------------------------------------- provider perms
    def _effective_perms_for_provider(self, user_obj, provider):
        """Frozen union of permissions the user holds against ``provider`` (the MSP itself).

        Only provider-scoped Roles contribute: tenant-scoped roles are evaluated per-tenant
        via :meth:`_effective_perms_for_tenant`.
        """
        cache_key = f'_perms_provider_{provider.pk}'
        if hasattr(user_obj, cache_key):
            return getattr(user_obj, cache_key)

        from organization.models import Membership, Role

        perms = set()
        staff = Membership.objects.filter(
            user=user_obj, provider=provider, is_active=True,
        ).first()
        if staff is not None:
            perms.update(staff.direct_permissions or [])
            for perm_list in Role._base_manager.filter(
                memberships=staff, deleted_at__isnull=True, scope=Role.SCOPE_PROVIDER,
            ).values_list('permissions', flat=True):
                perms.update(perm_list or [])

        result = frozenset(perms)
        setattr(user_obj, cache_key, result)
        return result

    # ------------------------------------------------------------------ scope check
    def _tenant_in_scope(self, staff_membership, tenant):
        """Whether ``tenant`` falls within the provider-staff membership's tenant scope.

        Thin delegate to the canonical :meth:`organization.models.Membership.covers_tenant`
        — kept as a method for the existing internal call site; do not re-implement the
        scope branching here.
        """
        return staff_membership.covers_tenant(tenant)

    # ------------------------------------------------------------------ context resolution
    def _resolve_target(self, user_obj, obj):
        """Decide whether to evaluate against a Provider or a Tenant context.

        Returns ``('provider', provider)``, ``('tenant', tenant)``, or ``(None, None)``.
        """
        from organization.models import Provider, Tenant, Membership

        if obj is not None:
            # Provider passed directly — provider context. Use strict isinstance against the
            # organization.Provider class (NOT a name-based match) so an unrelated model that
            # happens to be named ``Provider`` — e.g. ``subscriptions.Provider`` for SaaS
            # vendors — is not misinterpreted as the MSP provider here.
            if isinstance(obj, Provider):
                return 'provider', obj
            # A Tenant is ALWAYS tenant context, even though it carries a ``provider`` FK
            # (its managing MSP). Resolve it BEFORE the generic provider-attr sniff below;
            # otherwise ``has_perm(perm, obj=<provider-managed tenant>)`` is misrouted to
            # provider context and skips per-tenant resolution (including the manage_* strip).
            obj_tenant = getattr(obj, 'tenant', None)
            if obj_tenant is None and isinstance(obj, Tenant):
                obj_tenant = obj
            # A non-Tenant object carrying a provider FK (and no tenant) — provider context.
            if obj_tenant is None:
                obj_provider = getattr(obj, 'provider', None)
                if obj_provider is not None and isinstance(obj_provider, Provider):
                    return 'provider', obj_provider
            if obj_tenant is not None:
                # Pre-cache the (possibly None) active tenant membership for later perm calls.
                mem_cache_key = f'_tenant_membership_{obj_tenant.pk}'
                if not hasattr(user_obj, mem_cache_key):
                    membership = Membership.objects.filter(
                        user=user_obj, tenant=obj_tenant, is_active=True,
                    ).first()
                    setattr(user_obj, mem_cache_key, membership)
                return 'tenant', obj_tenant
            # Fall through to ambient resolution.

        # No obj: respect a bound membership for this user, else current tenant, else
        # first active tenant membership, else first reachable provider tenant.
        membership = get_current_membership()
        if membership and membership.user_id == user_obj.pk and membership.tenant_id:
            return 'tenant', membership.tenant
        current_tenant = get_current_tenant()
        if current_tenant is not None:
            return 'tenant', current_tenant
        first = Membership.objects.filter(
            user=user_obj, is_active=True, tenant__isnull=False,
        ).select_related('tenant').first()
        if first is not None:
            set_current_tenant(first.tenant)
            set_current_membership(first)
            return 'tenant', first.tenant
        # No tenant membership: fall back to a tenant reachable through provider staff.
        from organization.access import provider_accessible_tenant_ids
        provider_tenant_ids = provider_accessible_tenant_ids(user_obj)
        if provider_tenant_ids:
            from organization.models import Tenant
            tenant = Tenant._base_manager.filter(
                pk__in=provider_tenant_ids,
            ).order_by('name').first()
            if tenant is not None:
                set_current_tenant(tenant)
                return 'tenant', tenant
        # Or a Provider the user has staff membership in (for provider-only operators).
        first_staff = Membership.objects.filter(
            user=user_obj, is_active=True, provider__isnull=False,
        ).select_related('provider').first()
        if first_staff is not None:
            return 'provider', first_staff.provider
        return None, None

    # ------------------------------------------------------------------ public API
    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        kind, target = self._resolve_target(user_obj, obj)
        if target is None:
            return False
        if kind == 'provider':
            if perm in self._effective_perms_for_provider(user_obj, target):
                return True
            # Provider operators may also hold tenant-level perms via the same membership's
            # provider-scoped roles when checked against a Provider object — already covered.
            return False
        # kind == 'tenant'
        return perm in self._effective_perms_for_tenant(user_obj, target)

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        kind, target = self._resolve_target(user_obj, None)
        if target is None:
            return False
        prefix = app_label + '.'
        if kind == 'provider':
            return any(p.startswith(prefix) for p in self._effective_perms_for_provider(user_obj, target))
        return any(p.startswith(prefix) for p in self._effective_perms_for_tenant(user_obj, target))


# Backwards-compat alias: existing settings reference TenantMembershipBackend.
TenantMembershipBackend = MembershipBackend
