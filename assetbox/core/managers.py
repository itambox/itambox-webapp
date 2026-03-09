from django.db import models
from django.core.exceptions import FieldError, FieldDoesNotExist
from .querysets import CustomQuerySet
import contextvars

_current_tenant = contextvars.ContextVar('current_tenant', default=None)
_current_membership = contextvars.ContextVar('current_membership', default=None)

def set_current_tenant(tenant):
    _current_tenant.set(tenant)

def get_current_tenant():
    return _current_tenant.get()

def set_current_membership(membership):
    _current_membership.set(membership)

def get_current_membership():
    return _current_membership.get()



class CustomManager(models.Manager):
    """
    Base Manager that returns our CustomQuerySet.
    """
    def get_queryset(self):
        return CustomQuerySet(self.model, using=self._db)


class SoftDeleteQuerySet(models.QuerySet):
    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def active(self):
        return self.filter(deleted_at__isnull=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    pass


class TenantScopingQuerySet(models.QuerySet):
    def filter_by_tenant(self):
        active_tenant = get_current_tenant()
        if active_tenant:
            from django.apps import apps
            Tenant = apps.get_model('organization', 'Tenant')
            
            # Resolve allowed tenant IDs in the same TenantGroup (if the active tenant is in a group)
            if active_tenant.group_id:
                allowed_tenant_ids = Tenant._base_manager.filter(group_id=active_tenant.group_id).values_list('pk', flat=True)
            else:
                allowed_tenant_ids = [active_tenant.pk]

            # If the query is for the Tenant model itself:
            if self.model._meta.model_name == 'tenant':
                return self.filter(pk__in=allowed_tenant_ids)

            try:
                self.model._meta.get_field('tenant')
                try:
                    self.model._meta.get_field('filter_tenants')
                    return self.filter(
                        models.Q(tenant_id__in=allowed_tenant_ids) |
                        models.Q(filter_tenants__id__in=allowed_tenant_ids) |
                        (models.Q(tenant__isnull=True) & models.Q(filter_tenants__isnull=True))
                    ).distinct()
                except FieldDoesNotExist:
                    return self.filter(models.Q(tenant_id__in=allowed_tenant_ids) | models.Q(tenant__isnull=True))
            except FieldDoesNotExist:
                return self
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



