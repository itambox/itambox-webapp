from django.db import models
from django.core.exceptions import FieldError, FieldDoesNotExist
from django.db.models import QuerySet
from typing import Any, Optional, List
import contextvars

_current_tenant = contextvars.ContextVar('current_tenant', default=None)
_current_tenant_group = contextvars.ContextVar('current_tenant_group', default=None)
_current_membership = contextvars.ContextVar('current_membership', default=None)
# "All accessible tenants" scope for a non-superuser: no single tenant/group is
# active, yet the request is NOT global — it is scoped to exactly the tenants the
# canonical resolver authorizes (issue #29). Distinct from the superuser global
# scope (all three None + is_superuser) so it can never widen into it.
_current_all_accessible = contextvars.ContextVar('current_all_accessible', default=False)
_descendant_group_ids_cache = contextvars.ContextVar('descendant_group_ids_cache', default=None)

def set_current_tenant(tenant: Optional[Any]) -> None:
    _current_tenant.set(tenant)
    _descendant_group_ids_cache.set(None)

def get_current_tenant() -> Optional[Any]:
    return _current_tenant.get()

def set_current_tenant_group(group: Optional[Any]) -> None:
    _current_tenant_group.set(group)
    _descendant_group_ids_cache.set(None)

def get_current_tenant_group() -> Optional[Any]:
    return _current_tenant_group.get()

def set_current_membership(membership: Optional[Any]) -> None:
    _current_membership.set(membership)
    _descendant_group_ids_cache.set(None)

def get_current_membership() -> Optional[Any]:
    return _current_membership.get()


def set_current_all_accessible(flag: bool) -> None:
    _current_all_accessible.set(bool(flag))
    _descendant_group_ids_cache.set(None)


def get_current_all_accessible() -> bool:
    return _current_all_accessible.get()


def get_current_scope_conflict(user: Optional[Any]) -> bool:
    """True when more than one of tenant / group / all-accessible scope is
    active for an authenticated non-superuser.

    The session/middleware resolution, token authentication, and TaskContext
    each set at most one of these by construction, so a contradiction here
    means the contextvars were poked directly (a bug, or a background task
    inheriting stale ambient state from a wrapping request). Tenant-scoping
    consumers must fail closed to nothing in that case rather than silently
    prioritize one of the contradictory states — a superuser has no such
    ambiguity (they keep their own global/explicit-scope path regardless).
    """
    if (
        user is None
        or not getattr(user, 'is_authenticated', False)
        or getattr(user, 'is_superuser', False)
    ):
        return False
    active_states = (
        get_current_tenant(), get_current_tenant_group(), get_current_all_accessible(),
    )
    return sum(bool(state) for state in active_states) > 1



class SoftDeleteQuerySet(models.QuerySet):
    def deleted(self) -> QuerySet:
        return self.filter(deleted_at__isnull=False)

    def active(self) -> QuerySet:
        return self.filter(deleted_at__isnull=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    def get_queryset(self) -> QuerySet:
        qs = super().get_queryset()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    pass


class TenantScopingQuerySet(models.QuerySet):
    @staticmethod
    def _member_visible_group_ids(user):
        """The set of TenantGroup ids that contain a tenant ``user`` can access.

        Derived from the canonical ``accessible_tenant_ids`` (direct memberships +
        UserGroup-derived + managed reach), not direct Membership rows alone, so a
        member reaching a tenant only through a group grant or managed reach still
        sees that tenant's group. ``_base_manager`` (unscoped) keeps this off the
        tenant-scoped path and avoids recursion back into ``filter_by_tenant``.
        """
        from django.apps import apps
        # inline imports: avoid a core.managers -> organization import cycle at load.
        from organization.access import accessible_tenant_ids
        Tenant = apps.get_model('organization', 'Tenant')
        accessible = accessible_tenant_ids(user)
        group_ids = set(
            Tenant._base_manager.filter(pk__in=accessible)
            .values_list('group_id', flat=True)
        )
        group_ids.discard(None)
        return group_ids

    @staticmethod
    def _group_scope_tenant_ids(active_group, get_descendant_group_ids, Tenant):
        """Resolve allowed tenant ids for an active tenant-group scope.

        A member's group scope must cover EVERY tenant they can reach in the
        group — direct memberships, UserGroup-derived tenants, and
        managed-reach tenants — not just direct Membership rows, so a
        reach-only tenant does not vanish after "Show All". Intersects the
        canonical accessible set with the group subtree; superusers and
        system/anonymous contexts see the whole subtree.
        """
        allowed_group_ids = get_descendant_group_ids(active_group.pk)
        # inline import: avoid a core.managers -> itambox.middleware circular
        # import at module load.
        from itambox.middleware import get_current_user
        user = get_current_user()
        if user and user.is_superuser:
            return list(
                Tenant._base_manager.filter(
                    group_id__in=allowed_group_ids,
                    deleted_at__isnull=True,
                ).values_list('pk', flat=True)
            )
        if user:
            # inline import: avoid a core.managers -> organization cycle at load.
            from organization.access import accessible_tenant_ids
            accessible = accessible_tenant_ids(user)
            return list(
                Tenant._base_manager.filter(
                    pk__in=accessible, group_id__in=allowed_group_ids,
                    deleted_at__isnull=True,
                ).values_list('pk', flat=True)
            )
        return list(
            Tenant._base_manager.filter(
                group_id__in=allowed_group_ids,
                deleted_at__isnull=True,
            ).values_list('pk', flat=True)
        )

    def _resolve_allowed_tenant_ids(self, active_tenant, active_group, get_descendant_group_ids, Tenant):
        """Resolve the allowed tenant id set for whichever scope (single
        tenant / group / all-accessible) is currently active.
        """
        if active_tenant:
            return [active_tenant.pk]
        if active_group:
            return self._group_scope_tenant_ids(active_group, get_descendant_group_ids, Tenant)
        # "All accessible tenants" scope: no single tenant or group is active,
        # but the request is NOT global. This never returns the unscoped
        # queryset, so it can never widen into the superuser/global view.
        # inline import: avoid a core.managers -> itambox.middleware circular
        # import at module load.
        from itambox.middleware import get_current_user
        return self._all_accessible_tenant_ids(get_current_user())

    @staticmethod
    def _all_accessible_tenant_ids(user):
        """Resolve the "all accessible tenants" scope to EXACTLY the canonical
        accessible set (direct memberships, UserGroup-derived, and managed
        reach). Any principal that is not an authenticated non-superuser fails
        closed to no tenants — middleware only grants this scope to such
        members, and a superuser keeps their own global path.
        """
        if (
            user is not None
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
        ):
            # inline import: avoid a core.managers -> organization circular
            # import at module load.
            from organization.access import accessible_tenant_ids
            return list(accessible_tenant_ids(user))
        return []

    @staticmethod
    def _all_accessible_group_ids(user, allowed_tenant_ids, Tenant):
        """Live own/ancestor groups for the aggregate tenant set.

        Models with a ``tenant_group`` field all need the same projection. Cache
        it on the bound User instance so dozens of scoped querysets do not repeat
        one tenant-group query plus one query per ancestor depth. The shared
        authorization generation invalidates this memo on membership/grant or
        tenant/group-topology writes; a cache outage forces recomputation.
        """
        if user is None or not hasattr(user, '__dict__'):
            return frozenset()
        from core.auth.cache import synchronize_authorization_cache
        from organization.access import get_ancestor_tenant_group_ids

        synchronize_authorization_cache(user)
        tenant_key = tuple(sorted(allowed_tenant_ids))
        cached = user.__dict__.get('_all_accessible_group_ids')
        if cached is not None and cached[0] == tenant_key:
            return cached[1]

        own_group_ids = set(
            Tenant._base_manager.filter(pk__in=tenant_key)
            .exclude(group_id__isnull=True)
            .values_list('group_id', flat=True)
        )
        group_ids = set()
        for own_group_id in own_group_ids:
            group_ids |= get_ancestor_tenant_group_ids(
                own_group_id,
                live_only=True,
            )
        result = frozenset(group_ids)
        user.__dict__['_all_accessible_group_ids'] = (tenant_key, result)
        return result

    def filter_by_tenant(self) -> QuerySet:
        active_tenant = get_current_tenant()
        active_group = get_current_tenant_group()
        all_accessible = get_current_all_accessible()

        if active_tenant or active_group or all_accessible:
            # inline import: avoid a core.managers -> itambox.middleware circular
            # import at module load.
            from itambox.middleware import get_current_user
            current_user = get_current_user()
            if get_current_scope_conflict(current_user):
                return self.none()

            from django.apps import apps
            Tenant = apps.get_model('organization', 'Tenant')

            def get_descendant_group_ids(group_id):
                if not group_id:
                    return []
                cache = _descendant_group_ids_cache.get()
                if cache is None:
                    cache = {}
                    _descendant_group_ids_cache.set(cache)
                if group_id in cache:
                    return cache[group_id]

                TenantGroup = apps.get_model('organization', 'TenantGroup')
                descendant_ids = [group_id]
                seen = {group_id}
                to_check = [group_id]
                while to_check:
                    # _base_manager (unscoped): TenantGroup.objects is itself
                    # tenant-scoped now, so using it here would recurse back into
                    # filter_by_tenant. The descendant walk needs the true tree.
                    # exclude(seen): a parent cycle in bad data must terminate the
                    # walk, not hang every scoped request (mirrors the cycle-safe
                    # walk in organization.access.get_descendant_tenant_group_ids).
                    children = list(
                        TenantGroup._base_manager
                        .filter(parent_id__in=to_check, deleted_at__isnull=True)
                        .exclude(pk__in=seen)
                        .values_list('pk', flat=True)
                    )
                    if not children:
                        break
                    seen.update(children)
                    descendant_ids.extend(children)
                    to_check = children
                cache[group_id] = descendant_ids
                return descendant_ids

            allowed_tenant_ids = self._resolve_allowed_tenant_ids(
                active_tenant, active_group, get_descendant_group_ids, Tenant,
            )

            # If the query is for the Tenant model itself:
            if self.model._meta.model_name == 'tenant':
                return self.filter(pk__in=allowed_tenant_ids)

            # If the query is for the TenantGroup model itself: a user may see the
            # groups that contain a tenant they are a member of, plus those groups'
            # ancestors (the path to the root) for navigation. Superusers and
            # system/anonymous contexts see all. The parent walk uses
            # _base_manager so it does not recurse through this (scoped) manager.
            if self.model._meta.model_name == 'tenantgroup':
                # inline imports: avoid a core.managers -> middleware /
                # organization circular import at module load.
                tg_user = current_user
                TenantGroupModel = apps.get_model('organization', 'TenantGroup')

                def expand_to_ancestors(seed_ids):
                    # Walk parent links up to the root so the path to every visible
                    # group stays navigable. _base_manager (unscoped): don't recurse
                    # back through this (scoped) manager.
                    visible_ids = set()
                    frontier = set(seed_ids)
                    while frontier:
                        visible_ids |= frontier
                        parent_ids = set(
                            TenantGroupModel._base_manager
                            .filter(pk__in=frontier, deleted_at__isnull=True)
                            .values_list('parent_id', flat=True)
                        )
                        parent_ids.discard(None)
                        frontier = parent_ids - visible_ids
                    return visible_ids

                # An explicit group scope is a "show only this group" filter: the
                # TenantGroup list is restricted to the scoped group's subtree
                # (descendants) plus its ancestors (path to root, for navigation) —
                # for everyone, superusers included. This mirrors how the Tenant
                # list is already restricted to the scoped group's tenants under a
                # group scope. Without it, activating a group scope still leaked
                # every other (sibling/unrelated) group's row into the list.
                if active_group:
                    scope_ids = expand_to_ancestors(get_descendant_group_ids(active_group.pk))
                    if tg_user is None or getattr(tg_user, 'is_superuser', False):
                        return self.filter(pk__in=scope_ids)
                    # A member never sees a group none of their ACCESSIBLE tenants
                    # sit in (e.g. a descendant/sibling group inside the scoped
                    # subtree): intersect the scope with the groups of every tenant
                    # they can reach (direct, UserGroup, or managed) plus ancestors.
                    # The scoped group itself always survives — middleware only grants
                    # a group scope to a member who can access a tenant in it.
                    member_group_ids = self._member_visible_group_ids(tg_user)
                    return self.filter(pk__in=(scope_ids & expand_to_ancestors(member_group_ids)))

                # No explicit group scope (single-tenant scope): superusers and
                # system/anonymous contexts see all; a member sees the groups
                # containing a tenant they can ACCESS (direct, UserGroup, or
                # managed), plus those groups' ancestors.
                if tg_user is None or getattr(tg_user, 'is_superuser', False):
                    return self
                member_group_ids = self._member_visible_group_ids(tg_user)
                return self.filter(pk__in=expand_to_ancestors(member_group_ids))

            allowed_group_ids = []
            if active_group:
                allowed_group_ids = get_descendant_group_ids(active_group.pk)
            elif active_tenant and active_tenant.group:
                allowed_group_ids = get_descendant_group_ids(active_tenant.group.pk)

            qs = self

            # Filter by tenant group if field exists
            try:
                self.model._meta.get_field('tenant_group')
                group_ids = allowed_group_ids
                if all_accessible and not active_tenant and not active_group:
                    # Derived from the canonical accessible_tenant_ids, so no extra
                    # RBAC resolution; only runs for the (few) models that carry a
                    # tenant_group field.
                    group_ids = self._all_accessible_group_ids(
                        current_user,
                        allowed_tenant_ids,
                        Tenant,
                    )
                qs = qs.filter(models.Q(tenant_group_id__in=group_ids) | models.Q(tenant_group__isnull=True))
            except FieldDoesNotExist:
                pass

            # Filter by tenant if field exists
            try:
                self.model._meta.get_field('tenant')
                allow_global = getattr(self.model, 'allow_global_tenant', False)
                try:
                    self.model._meta.get_field('filter_tenants')
                    if allow_global:
                        qs = qs.filter(
                            models.Q(tenant_id__in=allowed_tenant_ids) |
                            models.Q(filter_tenants__id__in=allowed_tenant_ids) |
                            (models.Q(tenant__isnull=True) & models.Q(filter_tenants__isnull=True))
                        ).distinct()
                    else:
                        qs = qs.filter(
                            models.Q(tenant_id__in=allowed_tenant_ids) |
                            models.Q(filter_tenants__id__in=allowed_tenant_ids)
                        ).distinct()
                except FieldDoesNotExist:
                    if allow_global:
                        qs = qs.filter(models.Q(tenant_id__in=allowed_tenant_ids) | models.Q(tenant__isnull=True))
                    else:
                        qs = qs.filter(tenant_id__in=allowed_tenant_ids)
            except FieldDoesNotExist:
                # Models that derive their tenant through a relation rather than a
                # direct `tenant` field (e.g. assignments/stock keyed off their
                # parent item) declare `tenant_lookup`, an ORM path to the owning
                # tenant (e.g. 'asset__tenant'). Scope through it so these rows
                # cannot leak or be mutated across tenants. Rows whose parent has
                # no tenant (global/shared catalogue items) remain visible.
                tenant_lookup = getattr(self.model, 'tenant_lookup', None)
                if tenant_lookup:
                    # Children of a global (tenant=None) parent stay visible by
                    # default — e.g. stock/allocations of a shared-catalogue
                    # Component, or items of a global Kit template — because a
                    # global catalogue parent is a normal, intended pattern.
                    # Non-catalogue derived models that must NEVER be cross-tenant
                    # visible (e.g. LicenseSeatAssignment, where a global license
                    # is an anomaly an attacker can mint) opt OUT via
                    # `deny_global_tenant = True`, so a tenant=None parent does not
                    # expose the child to every tenant.
                    cond = models.Q(**{f'{tenant_lookup}_id__in': allowed_tenant_ids})
                    if not getattr(self.model, 'deny_global_tenant', False):
                        cond |= models.Q(**{f'{tenant_lookup}__isnull': True})
                    qs = qs.filter(cond)

            return qs

        # Fail closed: a request bound to an authenticated, non-superuser
        # principal that reaches this point has NO resolved tenant context.
        # Returning the unscoped queryset here would leak every tenant's rows
        # (and allow cross-tenant writes/deletes via .get(pk=...)). Scope it to
        # nothing instead. Superusers keep the global view, and system /
        # anonymous contexts (migrations, background tasks with no bound user,
        # the pre-tenant bootstrap in TenantMiddleware) are unaffected — those
        # paths legitimately operate without a tenant. Note Membership
        # uses the default (unscoped) manager, so tenant resolution itself is
        # not affected by this guard.
        from itambox.middleware import get_current_user
        user = get_current_user()
        if user is not None and not getattr(user, 'is_superuser', False):
            return self.none()
        return self


class TenantScopingManager(models.Manager.from_queryset(TenantScopingQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter_by_tenant()


class TenantScopingSoftDeleteQuerySet(SoftDeleteQuerySet, TenantScopingQuerySet):
    pass


class TenantScopingSoftDeleteManager(models.Manager.from_queryset(TenantScopingSoftDeleteQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset().filter_by_tenant()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class TenantScopingAllObjectsManager(models.Manager.from_queryset(TenantScopingSoftDeleteQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter_by_tenant()
