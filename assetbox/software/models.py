from django.db import models
from django.urls import reverse
from extras.models import Tag
from core.models import BaseModel, ChangeLoggingMixin # Import the mixin

class Software(ChangeLoggingMixin, BaseModel):
    """
    Represents a catalog entry for a software product.
    """
    name = models.CharField(
        max_length=255, 
        unique=True, 
        help_text="Unique name of the software product (e.g., Microsoft Visio Professional 2021)"
    )
    manufacturer = models.ForeignKey(
        to='assets.Manufacturer', # Use string reference here
        on_delete=models.PROTECT, # Prevent deleting manufacturer if software exists
        related_name='software_products' # Explicit related_name
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description of the software product."
    )
    tags = models.ManyToManyField(
        to=Tag,
        blank=True,
        related_name='software'
    )

    class Meta:
        ordering = ('manufacturer', 'name')
        verbose_name = "Software"
        verbose_name_plural = "Software"

    def __str__(self):
        return f"{self.manufacturer.name} - {self.name}"

    def get_absolute_url(self):
        return reverse('software:software_detail', kwargs={'pk': self.pk}) 