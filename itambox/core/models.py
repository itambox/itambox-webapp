# Standard library
from __future__ import annotations

# Third-party / Django
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

# Local application
from itambox.middleware import get_current_request_id, get_current_user
from itambox.registry import registry
from core.choices import ObjectChangeActionChoices, EventActionChoices, JobStatusChoices
from core.managers import TenantScopingManager, SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager
from core.mixins import (
    JournalingMixin, TaggableMixin,
    ImageAttachmentMixin, FileAttachmentMixin, ExportableMixin, CloneableMixin,
    SoftDeleteMixin
)
from core.utils import serialize_object
from core.validators import validate_image_attachment, validate_file_attachment


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        from core.validators import CustomValidator
        CustomValidator.validate_object(self)


class ObjectChange(models.Model):
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
        return reverse('objectchange', args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.object_type_repr:
            self.object_type_repr = f"{self.changed_object_type.app_label} | {self.changed_object_type.model}"
        super().save(*args, **kwargs)

    def get_changed_object_url(self):
        if hasattr(self.changed_object, 'get_absolute_url'):
            return self.changed_object.get_absolute_url()
        return None


class ChangeLoggingMixin:
    _change_logging_excluded_fields = ['updated_at']

    def __init__(self, *args, **kwargs):
        self._changelog_action = None
        self._changelog_message = ''
        self._prechange_snapshot = None
        super().__init__(*args, **kwargs)

    def clean(self):
        from core.validators import get_validators, parse_json_rules
        for validator in get_validators(self):
            if callable(validator):
                validator_instance = validator()
                validator_instance.validate(self)
            elif isinstance(validator, dict):
                parse_json_rules(self, validator)
        super().clean()

    def snapshot(self):
        self._prechange_snapshot = serialize_object(
            self, exclude_fields=self._change_logging_excluded_fields
        )

    def _log_change(self, action, prechange_data=None, postchange_data=None, message=''):
        user = get_current_user()
        request_id = get_current_request_id()

        if not request_id:
            return

        if user:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            if not User.objects.filter(pk=user.pk).exists():
                user = None

        ct = ContentType.objects.get_for_model(self.__class__)


        ObjectChange.objects.create(
            user=user,
            user_name=user.username if user else 'System',
            request_id=request_id,
            action=action,
            changed_object_type=ct,
            changed_object_id=self.pk,
            object_repr=str(self)[:200],
            object_type_repr=f"{ct.app_label} | {ct.model}",
            prechange_data=prechange_data,
            postchange_data=postchange_data,
        )

    def save(self, *args, **kwargs):
        if not get_current_request_id():
            super().save(*args, **kwargs)
            return

        is_creation = self._state.adding
        prechange_data = None

        if not is_creation:
            if self._prechange_snapshot is not None:
                prechange_data = self._prechange_snapshot
            else:
                # Use the unfiltered base manager: the changelog must capture the
                # pre-change state even when the active tenant context or soft-delete
                # filters would hide the row from the default manager.
                original_instance = self.__class__._base_manager.filter(pk=self.pk).first()
                if original_instance is not None:
                    prechange_data = serialize_object(original_instance, exclude_fields=self._change_logging_excluded_fields)

        super().save(*args, **kwargs)

        action = self._changelog_action or (
            ObjectChangeActionChoices.ACTION_CREATE if is_creation else ObjectChangeActionChoices.ACTION_UPDATE
        )
        postchange_data = serialize_object(self, exclude_fields=self._change_logging_excluded_fields)

        if action == ObjectChangeActionChoices.ACTION_UPDATE and prechange_data == postchange_data:
            return

        self._log_change(action=action, prechange_data=prechange_data, postchange_data=postchange_data, message=self._changelog_message)

    def delete(self, *args, **kwargs):
        # Peek at force_hard_delete without consuming it — SoftDeleteMixin owns
        # and consumes the flag wherever it sits in the MRO. (Never treat a
        # positional arg as the flag: positionally, delete()'s first arg is
        # Django's `using`.)
        from core.mixins import SoftDeleteMixin
        force_hard = bool(kwargs.get('force_hard_delete', False))

        if not get_current_request_id():
            super().delete(*args, **kwargs)
            return

        is_soft_delete = isinstance(self, SoftDeleteMixin) and not force_hard
        if is_soft_delete:
            super().delete(*args, **kwargs)
            return

        if self._prechange_snapshot is not None:
            prechange_data = self._prechange_snapshot
        else:
            prechange_data = serialize_object(self, exclude_fields=self._change_logging_excluded_fields)
        action = self._changelog_action or ObjectChangeActionChoices.ACTION_DELETE

        self._log_change(action=action, prechange_data=prechange_data, message=self._changelog_message)

        super().delete(*args, **kwargs)


    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        registry.register_feature(cls, 'change_logging')

    def get_changelog_url(self):
        ct = ContentType.objects.get_for_model(self.__class__)
        return reverse('objectchange_list') + '?' + urlencode({
            'changed_object_type': ct.pk,
            'changed_object_id': self.pk,
        })


class Notification(models.Model):
    LEVEL_INFO = 'info'
    LEVEL_WARNING = 'warning'
    LEVEL_SUCCESS = 'success'
    LEVEL_DANGER = 'danger'

    LEVEL_CHOICES = [
        (LEVEL_INFO, 'Info'),
        (LEVEL_WARNING, 'Warning'),
        (LEVEL_SUCCESS, 'Success'),
        (LEVEL_DANGER, 'Danger'),
    ]

    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True,
        blank=True,
        help_text="Target user for the notification. Null represents global broadcast alert."
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    is_read = models.BooleanField(default=False)
    target_url = models.CharField(max_length=500, blank=True, null=True, help_text="Optional destination URL when clicked.")
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        ordering = ('-created_at',)
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = [
            models.Index(fields=['user', 'is_read']),
        ]

    def __str__(self):
        return f"{self.subject} ({self.get_level_display()})"


class Job(ChangeLoggingMixin, BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = JobStatusChoices()

    name = models.CharField(max_length=255)
    model = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey('model', 'object_id')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    data = models.JSONField(default=dict, blank=True)
    result = models.JSONField(null=True, blank=True)
    logs = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    started = models.DateTimeField(null=True, blank=True)
    completed = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-created']
        verbose_name = "Job"
        verbose_name_plural = "Jobs"
        indexes = [
            models.Index(fields=['status', 'scheduled_for']),
        ]

    def __str__(self):
        return f"Job: {self.name} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('job_detail', kwargs={'pk': self.pk})

    def mark_running(self):
        self.status = self.STATUS_RUNNING
        self.started = timezone.now()
        self.save(update_fields=['status', 'started'])

    def mark_completed(self, result=None):
        self.status = self.STATUS_COMPLETED
        self.completed = timezone.now()
        if result is not None:
            self.result = result
        self.save(update_fields=['status', 'completed', 'result'])

    def mark_failed(self, error=None):
        self.status = self.STATUS_FAILED
        self.completed = timezone.now()
        if error:
            self.logs = f"{self.logs}\n{error}" if self.logs else str(error)
        self.save(update_fields=['status', 'completed', 'logs'])

    def append_log(self, message):
        timestamp = timezone.now().isoformat()
        entry = f"[{timestamp}] {message}"
        self.logs = f"{self.logs}\n{entry}" if self.logs else entry
        self.save(update_fields=['logs'])


class EmailSettings(ChangeLoggingMixin, BaseModel):
    smtp_host = models.CharField(max_length=255, default='localhost')
    smtp_port = models.PositiveIntegerField(default=25)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_username = models.CharField(max_length=255, blank=True, null=True)
    smtp_password = models.CharField(max_length=255, blank=True, null=True)
    from_address = models.EmailField(max_length=255, default='itambox@localhost')
    from_name = models.CharField(max_length=255, default='ITAMbox Notifications')
    enabled = models.BooleanField(default=False)
    test_recipient = models.EmailField(max_length=255, blank=True, null=True, help_text="Email address for test notifications")

    class Meta:
        verbose_name = "Email Settings"
        verbose_name_plural = "Email Settings"

    def __str__(self):
        return f"Email Settings ({'Enabled' if self.enabled else 'Disabled'})"

    def save(self, *args, **kwargs):
        # System-wide singleton: one outbound SMTP config for the whole install.
        # Per-tenant *destinations* are configured on NotificationChannel.config['recipients'].
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.first()





class StandardModel(JournalingMixin, TaggableMixin, ExportableMixin,
                    CloneableMixin, ChangeLoggingMixin, BaseModel):
    class Meta:
        abstract = True


class VaultModel(StandardModel, ImageAttachmentMixin, FileAttachmentMixin):
    class Meta:
        abstract = True


class DeletableVaultModel(VaultModel, SoftDeleteMixin):
    class Meta:
        abstract = True


class RecycleBin(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ('view_recyclebin', 'Can view Recycle Bin'),
            ('change_recyclebin', 'Can restore from Recycle Bin'),
            ('delete_recyclebin', 'Can purge from Recycle Bin'),
        ]



