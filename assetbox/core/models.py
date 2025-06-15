from django.conf import settings
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from core.choices import ObjectChangeActionChoices
from core.middleware import get_current_request_id, get_current_user
from core.utils import serialize_object # Import the serialization utility

# --- Base Model --- 

class BaseModel(models.Model):
    """
    Abstract base model providing timestamp fields.
    """
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True # Make this an abstract base class

# --- ObjectChange Model --- 

class ObjectChange(models.Model):
    """
    Records a change to an object instance. This is used for tracking modifications
    to objects for auditing and display purposes.
    """
    time = models.DateTimeField(
        default=timezone.now,
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
        editable=False
    )
    action = models.CharField(
        max_length=50,
        choices=ObjectChangeActionChoices(),
        db_index=True
    )
    changed_object_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.PROTECT,
        related_name='+'
    )
    changed_object_id = models.PositiveBigIntegerField(
        db_index=True
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
        null=True,
        db_index=True
    )
    related_object = GenericForeignKey(
        ct_field='related_object_type',
        fk_field='related_object_id'
    )
    object_repr = models.CharField(
        max_length=200,
        editable=False
    )
    prechange_data = models.JSONField(
        editable=False,
        blank=True,
        null=True
    )
    postchange_data = models.JSONField(
        editable=False,
        blank=True,
        null=True
    )

    # Denormalized label for the changed object type (e.g., "dcim | device")
    object_type_repr = models.CharField(
        max_length=100,
        editable=False,
        blank=True,
        null=True
    )

    class Meta:
        ordering = ['-time']
        indexes = [
            models.Index(fields=['changed_object_type', 'changed_object_id']),
            models.Index(fields=['related_object_type', 'related_object_id']),
        ]

    def __str__(self):
        action_label = self.get_action_display()
        return (
            f"{self.changed_object_type} {self.object_repr} {action_label} by "
            f"{self.user_name} at {self.time:%Y-%m-%d %H:%M:%S}"
        )

    def get_absolute_url(self):
        # Link to the objectchange detail view
        return reverse('objectchange', args=[self.pk])

    def save(self, *args, **kwargs):
        # Store a denormalized representation of the changed object's type
        if not self.object_type_repr:
            self.object_type_repr = f"{self.changed_object_type.app_label} | {self.changed_object_type.model}"
        super().save(*args, **kwargs)

    def get_changed_object_url(self):
        """
        Return the absolute URL for the changed object, if one exists.
        """
        if hasattr(self.changed_object, 'get_absolute_url'):
            return self.changed_object.get_absolute_url()
        return None 

class ChangeLoggingMixin:
    """
    Mixin to automatically log changes to a model instance.
    Creates ObjectChange records on save() and delete().
    """

    # Define fields to be ignored in the change log
    # Often includes 'updated_at' or other auto-updated fields
    _change_logging_excluded_fields = ['updated_at']

    def _log_change(self, action, prechange_data=None, postchange_data=None):
        """Helper method to create an ObjectChange record."""
        user = get_current_user()
        request_id = get_current_request_id()

        if not request_id:
            # Cannot log change if request ID is not available
            return

        # Get ContentType for the instance's class
        ct = ContentType.objects.get_for_model(self.__class__)

        ObjectChange.objects.create(
            user=user,
            user_name=user.username if user else 'System',
            request_id=request_id,
            action=action,
            changed_object_type=ct,
            changed_object_id=self.pk,
            object_repr=str(self)[:200], # Truncate representation
            object_type_repr=f"{ct.app_label} | {ct.model}", # Store denormalized type
            prechange_data=prechange_data,
            postchange_data=postchange_data,
        )

    def save(self, *args, **kwargs):
        """Override save() to log create/update changes."""
        is_creation = self._state.adding
        prechange_data = None

        if not is_creation:
            # Fetch the original state from DB for comparison
            try:
                original_instance = self.__class__.objects.get(pk=self.pk)
                # Serialize original state, excluding specified fields
                prechange_data = serialize_object(original_instance, extra_fields=self._change_logging_excluded_fields)
            except self.__class__.DoesNotExist:
                # Should not happen on update, but handle defensively
                pass
        
        # Perform the actual save operation first
        super().save(*args, **kwargs)

        # Now log the change
        action = ObjectChangeActionChoices.ACTION_CREATE if is_creation else ObjectChangeActionChoices.ACTION_UPDATE
        # Serialize current state, excluding specified fields
        postchange_data = serialize_object(self, extra_fields=self._change_logging_excluded_fields)
        
        # Only log update if data actually changed
        if action == ObjectChangeActionChoices.ACTION_UPDATE and prechange_data == postchange_data:
            return
            
        self._log_change(action=action, prechange_data=prechange_data, postchange_data=postchange_data)

    def delete(self, *args, **kwargs):
        """Override delete() to log deletion."""
        # Serialize current state before deleting
        prechange_data = serialize_object(self, extra_fields=self._change_logging_excluded_fields)
        action = ObjectChangeActionChoices.ACTION_DELETE
        
        # Log the change *before* performing delete, passing prechange_data
        self._log_change(action=action, prechange_data=prechange_data)
        
        # Perform the actual delete operation
        super().delete(*args, **kwargs) 