from django.conf import settings
from django.db import models
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin


class Tag(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    color = models.CharField(max_length=6, blank=True) # Store hex color without #
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:tag_detail', kwargs={'pk': self.pk})


class Dashboard(models.Model):
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


class CustomField(ChangeLoggingMixin, BaseModel):
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

    name = models.SlugField(max_length=50, unique=True, verbose_name="Field Name", help_text="Slug-like name (e.g. sim_card_number)")
    label = models.CharField(max_length=100, db_index=True, verbose_name="Display Label")
    field_type = models.CharField(max_length=50, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_TEXT, db_index=True, verbose_name="Field Type")
    choices = models.TextField(blank=True, null=True, help_text="New-line separated list of choices (only for 'select' type)")
    required = models.BooleanField(default=False, db_index=True, verbose_name="Required")

    class Meta:
        ordering = ['label']
        verbose_name = "Custom Field"
        verbose_name_plural = "Custom Fields"
        db_table = 'assets_customfield'
        app_label = 'assets'

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:customfield_detail', kwargs={'pk': self.pk})


class CustomFieldset(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Fieldset Name")
    fields = models.ManyToManyField(CustomField, related_name='fieldsets', blank=True, verbose_name="Custom Fields")

    class Meta:
        ordering = ['name']
        verbose_name = "Custom Fieldset"
        verbose_name_plural = "Custom Fieldsets"
        db_table = 'assets_customfieldset'
        app_label = 'assets'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:customfieldset_detail', kwargs={'pk': self.pk})


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

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:configcontext_edit', kwargs={'pk': self.pk})

