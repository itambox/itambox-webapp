from django.db import models
from django.core.exceptions import FieldError
from .querysets import CustomQuerySet
import contextvars

_current_tenant = contextvars.ContextVar('current_tenant', default=None)

def set_current_tenant(tenant):
    _current_tenant.set(tenant)

def get_current_tenant():
    return _current_tenant.get()


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
            try:
                self.model._meta.get_field('tenant')
                return self.filter(models.Q(tenant=active_tenant) | models.Q(tenant__isnull=True))
            except models.FieldDoesNotExist:
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



