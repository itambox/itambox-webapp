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


class NotificationChannel(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    TYPE_EMAIL = 'email'
    TYPE_IN_APP = 'in_app'
    TYPE_SLACK = 'slack'
    TYPE_TEAMS = 'teams'

    CHANNEL_TYPE_CHOICES = [
        (TYPE_EMAIL, 'Email'),
        (TYPE_IN_APP, 'In-App'),
        (TYPE_SLACK, 'Slack'),
        (TYPE_TEAMS, 'Microsoft Teams'),
    ]

    name = models.CharField(max_length=255)
    channel_type = models.CharField(max_length=20, choices=CHANNEL_TYPE_CHOICES)
    enabled = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True, help_text="Channel-specific config (SMTP settings, webhook URL, etc.)")
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='notification_channels',
        db_index=True,
        help_text="The tenant owning this channel. Null represents system-wide channels."
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Notification Channel"
        verbose_name_plural = "Notification Channels"
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_notificationchannel_name_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_channel_type_display()})"



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


class ReportTemplate(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    REPORT_TYPE_ASSET_SUMMARY = 'asset_summary'
    REPORT_TYPE_LICENSE_UTILIZATION = 'license_utilization'
    REPORT_TYPE_SUBSCRIPTION_RENEWALS = 'subscription_renewals'
    REPORT_TYPE_ASSET_MAINTENANCE = 'asset_maintenance'
    REPORT_TYPE_ASSET_DEPRECIATION = 'asset_depreciation'
    REPORT_TYPE_SOFTWARE_INVENTORY = 'software_inventory'

    REPORT_TYPE_CHOICES = [
        (REPORT_TYPE_ASSET_SUMMARY, 'Asset Inventory Summary'),
        (REPORT_TYPE_LICENSE_UTILIZATION, 'License Utilization'),
        (REPORT_TYPE_SUBSCRIPTION_RENEWALS, 'Subscription Renewals'),
        (REPORT_TYPE_ASSET_MAINTENANCE, 'Asset Maintenance & Repairs'),
        (REPORT_TYPE_ASSET_DEPRECIATION, 'Asset Depreciation Summary'),
        (REPORT_TYPE_SOFTWARE_INVENTORY, 'Software Catalog & Installations'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='report_templates',
        db_index=True,
        help_text="The tenant owning this report template. Null represents system-wide templates."
    )
    filter_tenants = models.ManyToManyField(
        'organization.Tenant',
        blank=True,
        related_name='filtered_templates',
        help_text="Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally."
    )
    report_type = models.CharField(max_length=50, choices=REPORT_TYPE_CHOICES)
    included_columns = models.JSONField(default=list, blank=True, help_text="Checked columns to render in the report data grid.")
    include_summary_cards = models.BooleanField(default=True, help_text="Toggle displaying top card widgets (totals, counts, financial sums).")
    include_distribution_chart = models.BooleanField(default=False, help_text="Toggle embedding spend or status distribution charts in the HTML report.")
    group_by_field = models.CharField(max_length=100, blank=True, null=True, help_text="Optional column key to group grid records under (e.g. location, status).")
    style_preset = models.CharField(max_length=50, default='default', choices=[
        ('default', 'Professional Layout'),
        ('compact', 'Compact Audit Sheet'),
        ('financial', 'Financial Spend Summary')
    ])
    advanced_mode = models.BooleanField(default=False, help_text="Enable custom Jinja2/HTML template code override.")
    template_content = models.TextField(
        blank=True,
        help_text="Optional Jinja2 custom HTML override template"
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Report Template"
        verbose_name_plural = "Report Templates"
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_reporttemplate_name_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_report_type_display()})"

    def get_absolute_url(self):
        return reverse('report_template_detail', kwargs={'pk': self.pk})

    def clean(self):
        super().clean()
        if self.template_content and self.template_content.strip():
            try:
                from jinja2 import Environment
                Environment().parse(self.template_content)
            except Exception as e:
                raise ValidationError({'template_content': f"Jinja2 template compilation failed: {str(e)}"})


class ScheduledReport(ChangeLoggingMixin, BaseModel):
    objects = TenantScopingManager()
    allow_global_tenant = True

    FORMAT_HTML = 'html'
    FORMAT_CSV = 'csv'
    FORMAT_CHOICES = [
        (FORMAT_HTML, 'HTML Email'),
        (FORMAT_CSV, 'CSV Attachment'),
    ]

    FREQUENCY_ONCE = 'once'
    FREQUENCY_HOURLY = 'hourly'
    FREQUENCY_DAILY = 'daily'
    FREQUENCY_WEEKLY = 'weekly'
    FREQUENCY_BIWEEKLY = 'biweekly'
    FREQUENCY_MONTHLY = 'monthly'
    FREQUENCY_QUARTERLY = 'quarterly'
    FREQUENCY_YEARLY = 'yearly'
    FREQUENCY_CRON = 'cron'

    FREQUENCY_CHOICES = [
        (FREQUENCY_ONCE, 'Once'),
        (FREQUENCY_HOURLY, 'Hourly'),
        (FREQUENCY_DAILY, 'Daily'),
        (FREQUENCY_WEEKLY, 'Weekly'),
        (FREQUENCY_BIWEEKLY, 'Biweekly'),
        (FREQUENCY_MONTHLY, 'Monthly'),
        (FREQUENCY_QUARTERLY, 'Quarterly'),
        (FREQUENCY_YEARLY, 'Yearly'),
        (FREQUENCY_CRON, 'Custom Cron Expression'),
    ]

    name = models.CharField(max_length=255)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='scheduled_reports',
        db_index=True,
        help_text="The tenant owning this scheduled report. Null represents system-wide schedules."
    )
    filter_tenants = models.ManyToManyField(
        'organization.Tenant',
        blank=True,
        related_name='filtered_schedules',
        help_text="Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally."
    )
    report = models.ForeignKey(ReportTemplate, on_delete=models.CASCADE, related_name='schedules')
    schedule = models.ForeignKey(
        'django_q.Schedule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_reports',
        help_text="Linked Django-Q Schedule"
    )
    recipients = models.TextField(blank=True, default='', help_text="Comma-separated email addresses")
    frequency = models.CharField(max_length=50, default='weekly', choices=FREQUENCY_CHOICES)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default=FORMAT_HTML)
    cron_expression = models.CharField(max_length=100, blank=True, null=True, help_text="Custom Cron Expression (e.g. '0 8 * * 1-5')")
    start_time = models.TimeField(null=True, blank=True, help_text="Time of day to run the schedule (e.g. 08:00:00)")
    channels = models.ManyToManyField('NotificationChannel', blank=True, related_name='scheduled_reports')
    save_to_archive = models.BooleanField(default=True, help_text="Store a copy of generated reports in the local file archive")
    is_active = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Scheduled Report"
        verbose_name_plural = "Scheduled Reports"

    def __str__(self):
        return f"{self.name} -> {self.report.name}"

    def delete(self, *args, **kwargs):
        if self.schedule:
            try:
                self.schedule.delete()
            except Exception:
                pass
        super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.frequency == 'cron':
            if not self.cron_expression:
                raise ValidationError({'cron_expression': "Cron expression is required when frequency is set to Custom Cron."})
            try:
                from croniter import croniter
                from django.utils import timezone
                croniter(self.cron_expression, timezone.now())
            except Exception as e:
                raise ValidationError({'cron_expression': f"Invalid Cron expression: {str(e)}"})
        if self.recipients:
            from django.core.validators import validate_email
            emails = [e.strip() for e in self.recipients.split(',') if e.strip()]
            if not emails:
                raise ValidationError({'recipients': "No recipient email addresses entered."})
            for email in emails:
                try:
                    validate_email(email)
                except ValidationError:
                    raise ValidationError({'recipients': f"'{email}' is not a valid email address."})


class ReportGenerationArchive(ChangeLoggingMixin, BaseModel):
    objects = TenantScopingManager()

    scheduled_report = models.ForeignKey(ScheduledReport, on_delete=models.CASCADE, related_name='archives')
    generated_at = models.DateTimeField(auto_now_add=True)
    format = models.CharField(max_length=20)
    status = models.CharField(max_length=50)
    error_message = models.TextField(blank=True, null=True)
    file = models.ForeignKey('extras.FileAttachment', on_delete=models.SET_NULL, null=True, blank=True, related_name='report_archives')
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='report_archives',
        db_index=True
    )

    class Meta:
        ordering = ['-generated_at']
        verbose_name = "Report Generation Archive"
        verbose_name_plural = "Report Generation Archives"

    def __str__(self):
        return f"{self.scheduled_report.name} - {self.generated_at:%Y-%m-%d %H:%M:%S}"



class AlertRule(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    ALERT_TYPE_LOW_STOCK = 'low_stock'
    ALERT_TYPE_UPCOMING_EOL = 'upcoming_eol'
    ALERT_TYPE_LICENSE_EXPIRY = 'license_expiry'
    ALERT_TYPE_RENEWAL_DUE = 'renewal_due'
    ALERT_TYPE_WARRANTY_EXPIRY = 'warranty_expiry'
    ALERT_TYPE_AUDIT_OVERDUE = 'audit_overdue'

    ALERT_TYPE_CHOICES = [
        (ALERT_TYPE_LOW_STOCK, 'Low Stock Alert'),
        (ALERT_TYPE_UPCOMING_EOL, 'Upcoming EOL Planning'),
        (ALERT_TYPE_LICENSE_EXPIRY, 'License Expiry Alert'),
        (ALERT_TYPE_RENEWAL_DUE, 'Renewal Due Alert'),
        (ALERT_TYPE_WARRANTY_EXPIRY, 'Warranty Expiry Alert'),
        (ALERT_TYPE_AUDIT_OVERDUE, 'Audit Overdue'),
    ]

    SEVERITY_INFO = 'info'
    SEVERITY_WARNING = 'warning'
    SEVERITY_CRITICAL = 'critical'

    SEVERITY_CHOICES = [
        (SEVERITY_INFO, 'Info'),
        (SEVERITY_WARNING, 'Warning'),
        (SEVERITY_CRITICAL, 'Critical'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    alert_type = models.CharField(max_length=50, choices=ALERT_TYPE_CHOICES)
    threshold_value = models.PositiveIntegerField(help_text="Limit count or days horizon")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_WARNING)
    is_active = models.BooleanField(default=True, help_text="Inactive rules are not evaluated at all.")
    is_muted = models.BooleanField(
        default=False,
        help_text="Muted rules still track alerts in the Alert Center but send no channel notifications.",
    )
    renotify_interval_days = models.PositiveIntegerField(
        default=0,
        help_text="0 = notify once. N = re-send channel notifications every N days while an alert stays unresolved.",
    )
    last_fired_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When this rule was last evaluated by the engine.",
    )
    channels = models.ManyToManyField('core.NotificationChannel', blank=True, related_name='alert_rules')
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='alert_rules',
        db_index=True,
        help_text="The tenant owning this rule. Null represents system-wide rules."
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Alert Rule"
        verbose_name_plural = "Alert Rules"
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_alertrule_name_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_alert_type_display()})"

    def get_absolute_url(self):
        return reverse('alert_rule_detail', kwargs={'pk': self.pk})


class AlertLog(BaseModel):
    objects = TenantScopingManager()

    STATUS_ACTIVE = 'active'
    STATUS_ACKNOWLEDGED = 'acknowledged'
    STATUS_RESOLVED = 'resolved'

    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_ACKNOWLEDGED, 'Acknowledged'),
        (STATUS_RESOLVED, 'Resolved'),
    ]

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name='logs')
    subject = models.CharField(max_length=255)
    message = models.TextField()
    severity = models.CharField(
        max_length=20,
        choices=AlertRule.SEVERITY_CHOICES,
        default=AlertRule.SEVERITY_WARNING,
        db_index=True,
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='alert_logs')
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    delivery_status = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-channel delivery result: {channel_pk: 'ok'|'failed'|'error: ...'}"
    )
    last_notified_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When channel notifications were last dispatched for this alert (drives re-notify).",
    )
    acknowledged_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_alerts'
    )
    resolved_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_alerts'
    )
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='alert_logs',
        db_index=True,
        help_text="The tenant owning this log. Null represents system-wide logs."
    )

    @property
    def content_object_safe(self):
        try:
            obj = self.content_object
            if obj is not None:
                return obj
            # Fallback for soft-deleted targets (bypass standard managers)
            if self.content_type and self.object_id:
                model_class = self.content_type.model_class()
                if hasattr(model_class, 'all_objects'):
                    return model_class.all_objects.filter(pk=self.object_id).first()
                return model_class.objects.filter(pk=self.object_id).first()
        except Exception:
            pass
        return None

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Alert Log"
        verbose_name_plural = "Alert Logs"
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['status']),
            models.Index(fields=['severity']),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject}"


class RecycleBin(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ('view_recyclebin', 'Can view Recycle Bin'),
            ('change_recyclebin', 'Can restore from Recycle Bin'),
            ('delete_recyclebin', 'Can purge from Recycle Bin'),
        ]



