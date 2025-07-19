from django.db import models
from .querysets import CustomQuerySet

class CustomManager(models.Manager):
    """
    Base Manager that returns our CustomQuerySet.
    """
    def get_queryset(self):
        return CustomQuerySet(self.model, using=self._db)
