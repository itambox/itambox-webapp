from django.db import models
from django.urls import reverse
from extras.models import Tag
from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import JournalingMixin, TaggableMixin, ExportableMixin, CloneableMixin, ImageAttachmentMixin, FileAttachmentMixin

class SoftwareCategoryChoices(models.TextChoices):
    OPERATING_SYSTEM = 'operating_system', 'Operating System'
    PRODUCTIVITY = 'productivity', 'Productivity'
    DEVELOPMENT = 'development', 'Development'
    SECURITY = 'security', 'Security'
    DESIGN = 'design', 'Design'
    OTHER = 'other', 'Other'

class SoftwareLicenseTypeChoices(models.TextChoices):
    PROPRIETARY = 'proprietary', 'Proprietary'
    OPEN_SOURCE = 'open_source', 'Open Source'
    FREEWARE = 'freeware', 'Freeware'
    SHAREWARE = 'shareware', 'Shareware'
    SUBSCRIPTION = 'subscription', 'Subscription'

class Software(JournalingMixin, TaggableMixin, CloneableMixin, ImageAttachmentMixin, FileAttachmentMixin, ExportableMixin, ChangeLoggingMixin, BaseModel):
    """
    Represents a catalog entry for a software product.
    """
    name = models.CharField(
        max_length=255, 
        unique=True, 
        help_text="Unique name of the software product (e.g., Microsoft Visio Professional 2021)"
    )
    manufacturer = models.ForeignKey(
        to='assets.Manufacturer',
        on_delete=models.PROTECT,
        related_name='software_products'
    )
    version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Current version (e.g., 2021, 16.0)"
    )
    category = models.CharField(
        max_length=50,
        choices=SoftwareCategoryChoices.choices,
        blank=True,
        db_index=True,
        help_text="Functional category"
    )
    license_type = models.CharField(
        max_length=50,
        choices=SoftwareLicenseTypeChoices.choices,
        blank=True,
        help_text="Default license type"
    )
    website = models.URLField(
        blank=True,
        help_text="Product homepage or vendor URL"
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

    @property
    def installed_count(self):
        from assets.models import InstalledSoftware
        return InstalledSoftware.objects.filter(software=self).count()

    @property
    def license_count(self):
        from licenses.models import License
        return License.objects.filter(software=self, deleted_at__isnull=True).count()