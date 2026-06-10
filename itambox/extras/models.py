from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from core.models import BaseModel, ChangeLoggingMixin
from core.managers import TenantScopingManager, SoftDeleteManager, AllObjectsManager
from core.mixins import SoftDeleteMixin


class Tag(ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
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
    objects = TenantScopingManager()
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
    choices = models.TextField(blank=True, null=True, help_text="New-line separated list of choices (only for 'select' type)")
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

