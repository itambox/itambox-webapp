"""AssetMaintenance — scheduled/completed maintenance records for assets."""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import (
    SoftDeleteMixin, JournalingMixin, TaggableMixin,
    CloneableMixin, ExportableMixin, ImageAttachmentMixin, FileAttachmentMixin,
)
from core.managers import TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager
from core.currency import CurrencyField
from assets.models.choices import MaintenanceStatusChoices


class AssetMaintenance(TaggableMixin, CloneableMixin, ExportableMixin,
                       JournalingMixin, ImageAttachmentMixin, FileAttachmentMixin,
                       SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    tenant_lookup = 'asset__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    MAINTENANCE_TYPE_UPGRADE = 'upgrade'
    MAINTENANCE_TYPE_REPAIR = 'repair'
    MAINTENANCE_TYPE_CALIBRATION = 'calibration'
    MAINTENANCE_TYPE_SOFTWARE_SUPPORT = 'software_support'
    MAINTENANCE_TYPE_HARDWARE_SUPPORT = 'hardware_support'
    MAINTENANCE_TYPE_CHOICES = [
        (MAINTENANCE_TYPE_UPGRADE, 'Upgrade'),
        (MAINTENANCE_TYPE_REPAIR, 'Repair'),
        (MAINTENANCE_TYPE_CALIBRATION, 'Calibration'),
        (MAINTENANCE_TYPE_SOFTWARE_SUPPORT, 'Software Support'),
        (MAINTENANCE_TYPE_HARDWARE_SUPPORT, 'Hardware Support'),
    ]

    asset = models.ForeignKey('assets.Asset', on_delete=models.PROTECT, related_name='maintenances', db_index=True)
    title = models.CharField(max_length=200, default='Maintenance')
    description = models.TextField(blank=True)
    supplier = models.ForeignKey('assets.Supplier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_("Supplier/Vendor"))
    performed_by = models.CharField(max_length=200, blank=True)
    maintenance_type = models.CharField(
        max_length=50,
        choices=MAINTENANCE_TYPE_CHOICES,
        default=MAINTENANCE_TYPE_REPAIR,
        verbose_name=_("Maintenance Type"),
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=MaintenanceStatusChoices.choices,
        default=MaintenanceStatusChoices.SCHEDULED,
        db_index=True
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Maintenance Cost")
    )
    currency = CurrencyField()
    start_date = models.DateField(verbose_name=_("Start Date"), db_index=True)
    completion_date = models.DateField(null=True, blank=True, verbose_name=_("Completion Date"), db_index=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_maintenances', blank=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = _("Asset Maintenance")
        verbose_name_plural = _("Asset Maintenances")

    def __str__(self):
        return f"{self.get_maintenance_type_display()} on {self.asset.name}"

    def get_absolute_url(self):
        return reverse('assets:assetmaintenance_detail', kwargs={'pk': self.pk})

    @property
    def downtime_days(self):
        if self.start_date and self.completion_date:
            return (self.completion_date - self.start_date).days
        return None
