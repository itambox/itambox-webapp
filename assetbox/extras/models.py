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
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dashboard'
    )
    layout = models.JSONField(
        default=list,
        blank=True,
        help_text='Ordered list of widget config dicts'
    )
    created = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user']

    def __str__(self):
        return f"Dashboard for {self.user.username}"

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
            self.save(update_fields=['layout'])

    def move_widget(self, from_index, to_index):
        """Reorder a widget within the layout."""
        layout = self.layout
        if 0 <= from_index < len(layout) and 0 <= to_index < len(layout):
            widget = layout.pop(from_index)
            layout.insert(to_index, widget)
            self.save(update_fields=['layout'])
