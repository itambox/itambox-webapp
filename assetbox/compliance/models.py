import secrets
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import BaseModel, ChangeLoggingMixin


def generate_token():
    return secrets.token_urlsafe(48)


class CustodyReceipt(ChangeLoggingMixin, BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    ACCEPTANCE_STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
    ]

    asset = models.ForeignKey('assets.Asset', on_delete=models.CASCADE, related_name='custody_receipts', db_index=True)
    holder = models.ForeignKey('organization.AssetHolder', on_delete=models.CASCADE, related_name='custody_receipts')
    token = models.CharField(max_length=64, unique=True, default=generate_token)
    accepted = models.BooleanField(default=False)
    accepted_date = models.DateTimeField(null=True, blank=True)
    acceptance_method = models.CharField(max_length=50, default='link')
    acceptance_status = models.CharField(max_length=20, choices=ACCEPTANCE_STATUS_CHOICES, default=STATUS_PENDING)
    signature_data = models.TextField(blank=True, null=True)
    signature_hash = models.CharField(max_length=64, blank=True, null=True)
    verification_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)
    signature_canvas = models.TextField(blank=True, null=True, help_text="Base64 canvas stroke vector string representation")
    signed_at = models.DateTimeField(default=timezone.now)
    eula_version = models.CharField(max_length=10, default='1.0')
    created_date = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ('-signed_at',)
        verbose_name = "Custody Receipt"
        verbose_name_plural = "Custody Receipts"
        db_table = 'assets_custodyreceipt'
        app_label = 'assets'

    def __str__(self):
        return f"Custody Receipt for {self.asset} signed by {self.holder} (EULA v{self.eula_version})"


class AssetMaintenance(ChangeLoggingMixin, BaseModel):
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

    asset = models.ForeignKey('assets.Asset', on_delete=models.CASCADE, related_name='maintenances', db_index=True)
    supplier = models.CharField(max_length=100, blank=True, verbose_name="Supplier/Vendor")
    maintenance_type = models.CharField(
        max_length=50,
        choices=MAINTENANCE_TYPE_CHOICES,
        default=MAINTENANCE_TYPE_REPAIR,
        verbose_name="Maintenance Type",
        db_index=True
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Maintenance Cost"
    )
    start_date = models.DateField(verbose_name="Start Date", db_index=True)
    completion_date = models.DateField(null=True, blank=True, verbose_name="Completion Date", db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = "Asset Maintenance"
        verbose_name_plural = "Asset Maintenances"
        db_table = 'assets_assetmaintenance'
        app_label = 'assets'

    def __str__(self):
        return f"{self.get_maintenance_type_display()} on {self.asset.name}"

    def get_absolute_url(self):
        return reverse('assets:assetmaintenance_detail', kwargs={'pk': self.pk})

    @property
    def downtime_days(self):
        if self.start_date and self.completion_date:
            return (self.completion_date - self.start_date).days
        return None
