from django.db import models
from django.core.exceptions import FieldError, FieldDoesNotExist
from django.db.models import QuerySet
from typing import Any, Optional, List
from .querysets import CustomQuerySet
import contextvars

_current_tenant = contextvars.ContextVar('current_tenant', default=None)
_current_tenant_group = contextvars.ContextVar('current_tenant_group', default=None)
_current_membership = contextvars.ContextVar('current_membership', default=None)
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



class CustomManager(models.Manager):
    """
    Base Manager that returns our CustomQuerySet.
    """
    def get_queryset(self):
        return CustomQuerySet(self.model, using=self._db)


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
                    children = list(TenantGroup.objects.filter(parent_id__in=to_check).values_list('pk', flat=True))
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
                    qs = qs.filter(
                        models.Q(**{f'{tenant_lookup}_id__in': allowed_tenant_ids}) |
                        models.Q(**{f'{tenant_lookup}__isnull': True})
                    )

            return qs
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




