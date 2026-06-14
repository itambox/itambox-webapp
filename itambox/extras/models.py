from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from core.models import BaseModel, ChangeLoggingMixin
from core.managers import (
    TenantScopingManager, SoftDeleteManager, AllObjectsManager,
    TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager,
)
from core.mixins import SoftDeleteMixin, BookmarkableMixin
from core.validators import validate_image_attachment, validate_file_attachment


class Tag(ChangeLoggingMixin, BaseModel, SoftDeleteMixin, BookmarkableMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    color = models.CharField(max_length=6, blank=True) # Store hex color without #
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_tag_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_tag_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:tag_detail', kwargs={'pk': self.pk})


class Dashboard(models.Model):
    # Dashboards are personal user objects — they are NOT tenant-scoped rows.
    # The `tenant` field only narrows widget data; it does not gate access to
    # the dashboard itself.  Using the plain manager ensures that
    # filter_by_tenant()'s fail-close (→ .none() when no tenant context) does
    # not prevent users from seeing their own dashboards.
    objects = models.Manager()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dashboards'
    )
    name = models.CharField(
        max_length=100,
        default='Main Dashboard'
    )
    is_default = models.BooleanField(
        default=False
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='dashboards',
        help_text='Scope all widgets on this dashboard to this specific tenant context.'
    )
    layout = models.JSONField(
        default=list,
        blank=True,
        help_text='Ordered list of widget config dicts'
    )
    created = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', 'name']
        verbose_name = _("Dashboard")
        verbose_name_plural = _("Dashboards")

    def __str__(self):
        if self.tenant:
            return f"{self.name} ({self.tenant.name}) for {self.user.username}"
        return f"{self.name} for {self.user.username}"

    def add_widget(self, widget_class, title=None, **config):
        """Add a new widget to the end of the layout."""
        entry = {
            'widget': widget_class,
            'title': title,
            'visible': True,
            'w': 4,
            'h': 2,
            'config': {},
            **config,
        }
        self.layout.append(entry)
        self.save(update_fields=['layout'])

    def remove_widget(self, index):
        """Remove a widget by its index in the layout list."""
        if 0 <= index < len(self.layout):
            self.layout.pop(index)
            self.save(update_fields=['layout'])

    def update_widget(self, index, **kwargs):
        """Update widget config at the given index."""
        if 0 <= index < len(self.layout):
            self.layout[index].update(kwargs)
            self.layout = list(self.layout)
            self.save(update_fields=['layout'])

    def move_widget(self, from_index, to_index):
        """Reorder a widget within the layout."""
        layout = self.layout
        if 0 <= from_index < len(layout) and 0 <= to_index < len(layout):
            widget = layout.pop(from_index)
            layout.insert(to_index, widget)
            self.save(update_fields=['layout'])


class CustomField(ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    FIELD_TYPE_TEXT = 'text'
    FIELD_TYPE_NUMBER = 'number'
    FIELD_TYPE_DATE = 'date'
    FIELD_TYPE_BOOLEAN = 'boolean'
    FIELD_TYPE_SELECT = 'select'
    FIELD_TYPE_CHOICES = [
        (FIELD_TYPE_TEXT, 'Text'),
        (FIELD_TYPE_NUMBER, 'Number'),
        (FIELD_TYPE_DATE, 'Date'),
        (FIELD_TYPE_BOOLEAN, 'Boolean'),
        (FIELD_TYPE_SELECT, 'Select / Dropdown'),
    ]

    name = models.SlugField(max_length=50, verbose_name="Field Name", help_text="Slug-like name (e.g. sim_card_number)")
    label = models.CharField(max_length=100, db_index=True, verbose_name="Display Label")
    field_type = models.CharField(max_length=50, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_TEXT, db_index=True, verbose_name="Field Type")
    choices = models.TextField(blank=True, help_text="New-line separated list of choices (only for 'select' type)")
    required = models.BooleanField(default=False, db_index=True, verbose_name="Required")
    object_types = models.ManyToManyField(
        'contenttypes.ContentType',
        related_name='custom_fields',
        blank=True,
        verbose_name="Object Types",
        help_text="The model(s) this field applies to. A field applying to Asset Type "
                  "describes a hardware specification; one applying to Asset describes "
                  "a per-device detail.",
    )

    class Meta:
        ordering = ['label']
        verbose_name = _("Custom Field")
        verbose_name_plural = _("Custom Fields")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_customfield_name_active'),
        ]

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"

    def get_absolute_url(self):
        return reverse('extras:customfield_detail', kwargs={'pk': self.pk})

    @property
    def is_asset_type_spec(self):
        """True when this field applies to AssetType (a hardware specification).
        Template-friendly replacement for the retired model_level flag."""
        return self.object_types.filter(app_label='assets', model='assettype').exists()


class CustomFieldset(ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, verbose_name="Fieldset Name")
    fields = models.ManyToManyField(CustomField, related_name='fieldsets', blank=True, verbose_name="Custom Fields")

    class Meta:
        ordering = ['name']
        verbose_name = _("Custom Fieldset")
        verbose_name_plural = _("Custom Fieldsets")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_customfieldset_name_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:customfieldset_detail', kwargs={'pk': self.pk})


class Event(ChangeLoggingMixin, BaseModel):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'

    ACTION_CHOICES = [
        (ACTION_CREATE, 'Create'),
        (ACTION_UPDATE, 'Update'),
        (ACTION_DELETE, 'Delete'),
    ]

    model = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='events')
    object_id = models.PositiveBigIntegerField(db_index=True)
    content_object = GenericForeignKey('model', 'object_id')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    data = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Event"
        verbose_name_plural = "Events"
        indexes = [
            models.Index(fields=['model', 'object_id'], name='core_event_model_i_6d722d_idx'),
            models.Index(fields=['processed', 'timestamp'], name='core_event_process_17ef77_idx'),
        ]

    def __str__(self):
        return f"Event {self.get_action_display()} on {self.content_object}"


class EventRule(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    ACTION_WEBHOOK = 'webhook'
    ACTION_NOTIFICATION = 'notification'

    ACTION_TYPE_CHOICES = [
        (ACTION_WEBHOOK, 'Webhook'),
        (ACTION_NOTIFICATION, 'Notification'),
    ]

    name = models.CharField(max_length=255)
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='event_rules')
    events = models.JSONField(
        default=list,
        help_text="List of event action types, e.g. ['create', 'update']"
    )
    conditions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional conditions for rule matching"
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPE_CHOICES)
    webhook = models.ForeignKey(
        'WebhookEndpoint',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_rules',
        help_text="Endpoint to call when the action type is Webhook. "
                  "Takes precedence over any 'url' in action_config.",
    )
    action_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Advanced/optional JSON config (notification body, header overrides, etc.)"
    )
    enabled = models.BooleanField(default=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='event_rules',
        db_index=True,
        help_text=_("The tenant owning this rule. Null represents system-wide rules."),
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Event Rule"
        verbose_name_plural = "Event Rules"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('eventrule_detail', kwargs={'pk': self.pk})

    def clean(self):
        super().clean()
        if (
            self.action_type == self.ACTION_WEBHOOK
            and self.webhook_id
            and self.tenant_id is not None
        ):
            endpoint_tenant_id = self.webhook.tenant_id
            if endpoint_tenant_id is not None and endpoint_tenant_id != self.tenant_id:
                raise ValidationError(
                    {'webhook': _("Webhook endpoint must belong to the same tenant as the rule, or be system-wide.")}
                )


class WebhookEndpoint(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    HTTP_GET = 'GET'
    HTTP_POST = 'POST'
    HTTP_PUT = 'PUT'
    HTTP_PATCH = 'PATCH'
    METHOD_CHOICES = [
        (HTTP_GET, 'GET'),
        (HTTP_POST, 'POST'),
        (HTTP_PUT, 'PUT'),
        (HTTP_PATCH, 'PATCH'),
    ]

    name = models.CharField(max_length=255)
    url = models.URLField(max_length=2000)
    http_method = models.CharField(max_length=10, choices=METHOD_CHOICES, default=HTTP_POST)
    headers = models.JSONField(default=dict, blank=True)
    secret = models.CharField(max_length=255, blank=True, help_text="Shared secret for HMAC payload signing")
    enabled = models.BooleanField(default=True)
    retry_count = models.PositiveSmallIntegerField(default=3, help_text="Max retry attempts on failure")
    retry_backoff = models.PositiveSmallIntegerField(default=60, help_text="Backoff in seconds between retries")
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='webhook_endpoints',
        db_index=True,
        help_text=_("The tenant owning this endpoint. Null represents system-wide endpoints."),
    )

    class Meta:
        ordering = ['name']
        verbose_name = "Webhook Endpoint"
        verbose_name_plural = "Webhook Endpoints"
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_webhookendpoint_tenant_name_active'
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('webhookendpoint_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        if self.secret and not self.secret.startswith("enc$"):
            from core.crypto import encrypt_string
            self.secret = encrypt_string(self.secret)
        super().save(*args, **kwargs)

    @property
    def secret_decrypted(self) -> str:
        if not self.secret:
            return ""
        if self.secret.startswith("enc$"):
            from core.crypto import decrypt_string
            return decrypt_string(self.secret)
        return self.secret




class JournalEntry(ChangeLoggingMixin, BaseModel):
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='journal_entries')
    object_id = models.PositiveBigIntegerField(db_index=True)
    content_object = GenericForeignKey('model', 'object_id')
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='journal_entries'
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    comment = models.TextField()

    class Meta:
        ordering = ['-created']
        verbose_name = "Journal Entry"
        verbose_name_plural = "Journal Entries"
        indexes = [
            models.Index(fields=['model', 'object_id'], name='core_journa_model_i_3f2f97_idx'),
        ]

    def __str__(self):
        return f"Journal entry on {self.content_object} by {self.user}"


class Bookmark(ChangeLoggingMixin, BaseModel):
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bookmarks'
    )
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey('model', 'object_id')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
        verbose_name = "Bookmark"
        verbose_name_plural = "Bookmarks"
        indexes = [
            models.Index(fields=['user', 'model', 'object_id'], name='core_bookma_user_id_69a2d6_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'model', 'object_id'],
                name='core_bookmark_unique_user_model_object'
            )
        ]

    def __str__(self):
        return f"Bookmark by {self.user} on {self.content_object}"


class ObjectWatch(ChangeLoggingMixin, BaseModel):
    """Notify the user on every change to the watched object (bell / Watch feature)."""
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watches'
    )
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey('model', 'object_id')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
        verbose_name = "Object Watch"
        verbose_name_plural = "Object Watches"
        indexes = [
            models.Index(fields=['user', 'model', 'object_id'], name='extras_watch_user_id_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'model', 'object_id'],
                name='extras_objectwatch_unique_user_model_object'
            )
        ]

    def __str__(self):
        return f"Watch by {self.user} on {self.content_object}"


class ImageAttachment(ChangeLoggingMixin, BaseModel):
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='image_attachments')
    object_id = models.PositiveBigIntegerField(db_index=True)
    content_object = GenericForeignKey('model', 'object_id')
    image = models.ImageField(upload_to='attachments/images/', validators=[validate_image_attachment])
    name = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
        verbose_name = "Image Attachment"
        verbose_name_plural = "Image Attachments"
        indexes = [
            models.Index(fields=['model', 'object_id'], name='core_imagea_model_i_684849_idx'),
        ]

    def __str__(self):
        return self.name or f"Image {self.pk}"

    def get_serve_url(self):
        # Serve through the authenticated, tenant-scoped proxy rather than the
        # raw MEDIA_URL (which the web server exposes with no access control).
        return reverse('image_attachment_serve', kwargs={'pk': self.pk})


class FileAttachment(ChangeLoggingMixin, BaseModel):
    model = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='file_attachments')
    object_id = models.PositiveBigIntegerField(db_index=True)
    content_object = GenericForeignKey('model', 'object_id')
    file = models.FileField(upload_to='attachments/files/', validators=[validate_file_attachment])
    name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
        verbose_name = "File Attachment"
        verbose_name_plural = "File Attachments"
        indexes = [
            models.Index(fields=['model', 'object_id'], name='core_fileat_model_i_c8edb4_idx'),
        ]

    def __str__(self):
        return self.name or f"File {self.pk}"

    def get_download_url(self):
        # Download through the authenticated, tenant-scoped proxy (forces
        # attachment + nosniff) instead of the raw MEDIA_URL.
        return reverse('file_attachment_download', kwargs={'pk': self.pk})


class ExportTemplate(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='export_templates')
    template_code = models.TextField(help_text="Jinja2 or Django template code for export")
    mime_type = models.CharField(max_length=50, default='text/csv', help_text="MIME type for the exported file")
    file_extension = models.CharField(max_length=10, default='csv')

    class Meta:
        ordering = ['content_type', 'name']
        verbose_name = "Export Template"
        verbose_name_plural = "Export Templates"
        constraints = [
            models.UniqueConstraint(
                fields=['content_type', 'name'],
                name='core_exporttemplate_unique_content_type_name'
            )
        ]

    def __str__(self):
        return f"{self.content_type.model} - {self.name}"

    def get_absolute_url(self):
        return reverse('export_template_detail', kwargs={'pk': self.pk})

    def render(self, obj):
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment()
        template = env.from_string(self.template_code)
        return template.render(obj=obj)

    def render_queryset(self, queryset):
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment()
        template = env.from_string(self.template_code)
        results = []
        for obj in queryset:
            results.append(template.render(obj=obj))
        return '\n'.join(results)


class LabelTemplate(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    page_width = models.FloatField(default=2.25, help_text="Label width in inches")
    page_height = models.FloatField(default=1.25, help_text="Label height in inches")
    barcode_format = models.CharField(max_length=20, default='code128', choices=[
        ('code128', 'Code 128'),
        ('code39', 'Code 39'),
        ('qr', 'QR Code'),
        ('datamatrix', 'Data Matrix'),
    ])
    template_code = models.TextField(blank=True, help_text="Jinja2/HTML template for label layout")

    class Meta:
        ordering = ['name']
        verbose_name = "Label Template"
        verbose_name_plural = "Label Templates"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('labeltemplate_detail', kwargs={'pk': self.pk})


class ConfigContext(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    weight = models.PositiveSmallIntegerField(
        default=100,
        help_text="Priority weight for dictionary merging conflict resolution"
    )
    regions = models.ManyToManyField('organization.Region', blank=True, related_name='config_contexts')
    sites = models.ManyToManyField('organization.Site', blank=True, related_name='config_contexts')
    locations = models.ManyToManyField('organization.Location', blank=True, related_name='config_contexts')
    tenants = models.ManyToManyField('organization.Tenant', blank=True, related_name='config_contexts')
    data = models.JSONField(help_text="Serialized configuration dictionary")

    class Meta:
        ordering = ['weight', 'name']
        verbose_name = _("Config Context")
        verbose_name_plural = _("Config Contexts")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:configcontext_edit', kwargs={'pk': self.pk})


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
    group_by_field = models.CharField(max_length=100, blank=True, help_text="Optional column key to group grid records under (e.g. location, status).")
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
    cron_expression = models.CharField(max_length=100, blank=True, help_text="Custom Cron Expression (e.g. '0 8 * * 1-5')")
    start_time = models.TimeField(null=True, blank=True, help_text="Time of day to run the schedule (e.g. 08:00:00)")
    channels = models.ManyToManyField('extras.NotificationChannel', blank=True, related_name='scheduled_reports')
    save_to_archive = models.BooleanField(default=True, help_text="Store a copy of generated reports in the local file archive")
    is_active = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, blank=True)

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
    error_message = models.TextField(blank=True)
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


class NotificationChannel(ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

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
    channels = models.ManyToManyField(NotificationChannel, blank=True, related_name='alert_rules')
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
        from django.urls import reverse
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
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE, related_name='alert_logs')
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
            models.Index(fields=['content_type', 'object_id'], name='core_alertl_content_706751_idx'),
            models.Index(fields=['severity'], name='core_alertl_severit_f0ec11_idx'),
            models.Index(fields=['status'], name='core_alertl_status_b2f47a_idx'),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject}"

