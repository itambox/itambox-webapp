from django.db import models
from .querysets import CustomQuerySet

class CustomManager(models.Manager):
    """
    Base Manager that returns our CustomQuerySet.
    """
    def get_queryset(self):
        return CustomQuerySet(self.model, using=self._db)

    # You can add Manager-specific methods here if needed in the future,
    # mirroring methods from the CustomQuerySet.
    # For example:
    # def add_related_count(self, *args, **kwargs):
    #     return self.get_queryset().add_related_count(*args, **kwargs) 