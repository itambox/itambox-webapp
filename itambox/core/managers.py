from django.db import models
from django.core.exceptions import FieldError, FieldDoesNotExist
from django.db.models import QuerySet
from typing import Any, Optional, List
import contextvars

_current_tenant = contextvars.ContextVar('current_tenant', default=None)
_current_tenant_group = contextvars.ContextVar('current_tenant_group', default=None)
_current_membership = contextvars.ContextVar('current_membership', default=None)
_current_provider_membership = contextvars.ContextVar('current_provider_membership', default=None)
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

def set_current_provider_membership(membership: Optional[Any]) -> None:
    # The user's ProviderMembership for the active tenant's provider (if any). Set by
    # TenantMiddleware; available to views that need provider context. Does not affect
    # ORM tenant scoping (provider staff still operate one tenant at a time).
    _current_provider_membership.set(membership)

def get_current_provider_membership() -> Optional[Any]:
    return _current_provider_membership.get()



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
    def filter_by_tenant(self) -> QuerySet:
        active_tenant = get_current_tenant()
        active_group = get_current_tenant_group()

        if active_tenant or active_group:
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
                to_check = [group_id]
                while to_check:
                    # _base_manager (unscoped): TenantGroup.objects is itself
                    # tenant-scoped now, so using it here would recurse back into
                    # filter_by_tenant. The descendant walk needs the true tree.
                    children = list(TenantGroup._base_manager.filter(parent_id__in=to_check, deleted_at__isnull=True).values_list('pk', flat=True))
                    if not children:
                        break
                    descendant_ids.extend(children)
                    to_check = children
                cache[group_id] = descendant_ids
                return descendant_ids

            if active_tenant:
                allowed_tenant_ids = [active_tenant.pk]
            else:
                allowed_group_ids = get_descendant_group_ids(active_group.pk)
                from itambox.middleware import get_current_user
                user = get_current_user()
                if user and user.is_superuser:
                    allowed_tenant_ids = list(Tenant._base_manager.filter(group_id__in=allowed_group_ids).values_list('pk', flat=True))
                elif user:
                    from organization.models import TenantMembership
                    allowed_tenant_ids = list(TenantMembership.objects.filter(user=user, tenant__group_id__in=allowed_group_ids).values_list('tenant_id', flat=True))
                else:
                    allowed_tenant_ids = list(Tenant._base_manager.filter(group_id__in=allowed_group_ids).values_list('pk', flat=True))

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
                from itambox.middleware import get_current_user
                tg_user = get_current_user()
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
                    # A member never sees a group they hold no membership in (e.g. a
                    # descendant/sibling group inside the scoped subtree): intersect
                    # the scope with their own accessible groups + ancestors. The
                    # scoped group itself always survives — middleware only grants a
                    # group scope to a member with a tenant directly in that group.
                    from organization.models import TenantMembership
                    member_group_ids = set(
                        TenantMembership.objects.filter(user=tg_user)
                        .values_list('tenant__group_id', flat=True)
                    )
                    member_group_ids.discard(None)
                    return self.filter(pk__in=(scope_ids & expand_to_ancestors(member_group_ids)))

                # No explicit group scope (single-tenant scope): superusers and
                # system/anonymous contexts see all; a member sees the groups
                # containing a tenant they belong to, plus those groups' ancestors.
                if tg_user is None or getattr(tg_user, 'is_superuser', False):
                    return self
                from organization.models import TenantMembership
                member_group_ids = set(
                    TenantMembership.objects.filter(user=tg_user)
                    .values_list('tenant__group_id', flat=True)
                )
                member_group_ids.discard(None)
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
                qs = qs.filter(models.Q(tenant_group_id__in=allowed_group_ids) | models.Q(tenant_group__isnull=True))
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
        # paths legitimately operate without a tenant. Note TenantMembership
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




