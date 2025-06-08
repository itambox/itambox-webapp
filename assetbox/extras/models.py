from django.db import models
from django.utils.text import slugify
from django.urls import reverse

# Create your models here.

class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    color = models.CharField(max_length=6, blank=True) # Store hex color without #
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Assuming a detail view exists in the 'extras' app
        # Update to use PK to match the URL pattern
        return reverse('extras:tag_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        # Auto-generate slug if it's not set
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
