from django.db import models
from django.utils.text import slugify
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin

# Create your models here.

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
        # Assuming a detail view exists in the 'extras' app
        # Update to use PK to match the URL pattern
        return reverse('extras:tag_detail', kwargs={'pk': self.pk})


class ConfigTemplate(models.Model):
    """
    Configuration templates for assets, enabling consistent configuration management.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    template_content = models.TextField(help_text="Configuration template content")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Config Template"
        verbose_name_plural = "Config Templates"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('extras:configtemplate_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
