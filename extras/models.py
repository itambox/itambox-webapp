from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
import uuid

# Assuming your Tag model is already defined here or imported
# from .models import Tag # Or wherever Tag is defined

# --- ObjectChange Model (Inspired by NetBox) --- #

class ObjectChangeActionChoices(models.TextChoices):
    ACTION_CREATE = 'create', 'Created'
    ACTION_UPDATE = 'update', 'Updated'
    ACTION_DELETE = 'delete', 'Deleted'

class ObjectChange(models.Model):
    """
    Record of an object being created, updated, or deleted.
    """
    time = models.DateTimeField(
        auto_now_add=True,
        editable=False,
        db_index=True
    )
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='changes',
        blank=True,
        null=True
    )
    user_name = models.CharField(
        max_length=150,
        editable=False
    )
    request_id = models.UUIDField(
        editable=False,
        db_index=True # Index for faster lookups per request
    )
    action = models.CharField(
        max_length=10,
        choices=ObjectChangeActionChoices.choices,
        db_index=True # Index for filtering by action
    )
    changed_object_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.PROTECT,
        related_name='+',
        db_index=True # Index for filtering by type
    )
    changed_object_id = models.PositiveBigIntegerField(
        # Using BigIntegerField to be safe for future PK types
    )
    changed_object = GenericForeignKey(
        ct_field='changed_object_type',
        fk_field='changed_object_id'
    )
    related_object_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.PROTECT,
        related_name='+',
        blank=True,
        null=True
    )
    related_object_id = models.PositiveBigIntegerField(
        blank=True,
        null=True
    )
    related_object = GenericForeignKey(
        ct_field='related_object_type',
        fk_field='related_object_id'
    )
    object_repr = models.CharField(
        max_length=200,
        editable=False
    )
    object_data = models.JSONField(
        editable=False
    )

    csv_headers = ['time', 'user_name', 'action', 'changed_object_type', 'object_repr', 'request_id']

    class Meta:
        ordering = ['time']
        verbose_name = "Object Change"
        verbose_name_plural = "Object Changes"
        # Index for common lookup: type and ID
        indexes = [
            models.Index(fields=['changed_object_type', 'changed_object_id']),
        ]

    def __str__(self):
        return f'{self.changed_object_type} {self.object_repr} {self.get_action_display()} by {self.user_name} @ {self.time}'

    def get_absolute_url(self):
        return reverse('extras:objectchange_detail', kwargs={'pk': self.pk}) # Assuming a detail view name

    def save(self, *args, **kwargs):
        # Store the user's username for durability
        if self.user and not self.user_name:
            self.user_name = self.user.username

        return super().save(*args, **kwargs)

    # Helper methods like NetBox (get_changed_object_url, etc.) can be added later

# --- End ObjectChange Model --- # 