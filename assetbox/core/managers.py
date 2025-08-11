from django.db import models
from django.core.exceptions import FieldError
from .querysets import CustomQuerySet

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

