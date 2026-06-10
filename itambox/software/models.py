from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from extras.models import Tag
from core.models import BaseModel, ChangeLoggingMixin, VaultModel, DeletableVaultModel
from core.mixins import CustomFieldDataMixin
from core.managers import SoftDeleteManager, AllObjectsManager
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

class Software(CustomFieldDataMixin, DeletableVaultModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

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
        verbose_name = _("Software")
        verbose_name_plural = _("Software")

    def __str__(self):
        return f"{self.manufacturer.name} - {self.name}"

    def get_absolute_url(self):
        return reverse('software:software_detail', kwargs={'pk': self.pk})

    @property
    def installed_count(self):
        return InstalledSoftware.objects.filter(software=self).count()

    @property
    def license_count(self):
        from licenses.models import License
        return License.objects.filter(software=self, deleted_at__isnull=True).count()


class InstalledSoftware(ChangeLoggingMixin, BaseModel):
    """
    Represents an instance of software discovered or inventoried on a specific asset.
    Distinct from license assignment/tracking.
    """
    asset = models.ForeignKey(
        to='assets.Asset',
        on_delete=models.CASCADE,
        related_name='installed_software',
        db_index=True
    )
    software = models.ForeignKey(
        to=Software,
        on_delete=models.PROTECT,
        related_name='installed_instances'
    )
    version_detected = models.CharField(
        max_length=100,
        blank=True,
        help_text="Specific version discovered on the asset (e.g., 16.78.1)"
    )
    install_date = models.DateField(
        blank=True,
        null=True,
        db_index=True,
        help_text="Estimated or known installation date"
    )
    discovered_by_agent = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Discovered By",
        help_text="Identifier for the discovery source or agent (e.g., SCCM, Intune, Lansweeper)"
    )
    last_seen_date = models.DateTimeField(
        blank=True,
        null=True,
        db_index=True,
        help_text="Timestamp when this software was last detected on the asset"
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional notes specific to this installation"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['asset', 'software', 'version_detected'], name='unique_asset_software_version')
        ]
        ordering = ['asset', 'software', '-last_seen_date']
        verbose_name = _("Installed Software Instance")
        verbose_name_plural = _("Installed Software Instances")

    def __str__(self):
        version_part = f" (v{self.version_detected})" if self.version_detected else ""
        return f"{self.software.name}{version_part} on {self.asset.name}"

    def get_absolute_url(self):
        return self.asset.get_absolute_url()