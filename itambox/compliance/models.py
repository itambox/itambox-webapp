import secrets
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.utils import timezone

from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import TaggableMixin, CloneableMixin, ExportableMixin, JournalingMixin, ImageAttachmentMixin, FileAttachmentMixin, SoftDeleteMixin
from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingManager, TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager


def generate_token():
    return secrets.token_urlsafe(48)


class MaintenanceStatusChoices(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


class CustodyTemplate(TaggableMixin, CloneableMixin, ExportableMixin, ChangeLoggingMixin, SoftDeleteMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    tenant = models.ForeignKey(
        to='organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='custody_templates'
    )
    tenant_group = models.ForeignKey(
        to='organization.TenantGroup',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='custody_templates',
        help_text="Target Tenant Group this template applies to (conglomerate/divisional scope)."
    )
    name = models.CharField(
        max_length=255,
        help_text="Template Name (e.g. Standard Laptop EULA)"
    )
    signature_provider = models.CharField(
        max_length=50,
        default='local',
        help_text="E-Signature workflow provider module"
    )
    logo = models.ImageField(
        upload_to='custody_logos/',
        blank=True,
        null=True,
        help_text="Custom EULA / signoff logo image"
    )
    eula_text = models.TextField(
        help_text="Terms of Service / EULA guidelines"
    )
    disclaimer = models.TextField(
        blank=True,
        help_text="Disclaimer statement printed at signoff"
    )
    qms_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Quality Management System document reference key"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Deactivate to hide from choices"
    )
    require_acceptance = models.BooleanField(
        default=True,
        help_text="Require digital signature / EULA acceptance on checkout."
    )
    email_signature_request = models.BooleanField(
        default=True,
        help_text="Send email signature request link to the holder on checkout."
    )
    category = models.ForeignKey(
        to='assets.Category',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='custody_templates',
        help_text="Target Category this template overrides for the selected Tenant scope."
    )
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='custody_templates',
        blank=True
    )

    class Meta:
        ordering = ('name',)
        verbose_name = _("Custody Template")
        verbose_name_plural = _("Custody Templates")

    def __str__(self):
        if self.tenant:
            return f"{self.tenant} - {self.name}"
        if self.tenant_group:
            return f"Group: {self.tenant_group} - {self.name}"
        return f"Global - {self.name}"


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
    custody_template = models.ForeignKey(
        to='compliance.CustodyTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='receipts'
    )
    signature_provider = models.CharField(max_length=50, default='local')
    eula_text = models.TextField(blank=True, null=True)
    disclaimer = models.TextField(blank=True, null=True)
    qms_reference = models.CharField(max_length=100, blank=True, null=True)
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
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ('-signed_at',)
        verbose_name = _("Custody Receipt")
        verbose_name_plural = _("Custody Receipts")

    def __str__(self):
        return f"Custody Receipt for {self.asset} signed by {self.holder} (EULA v{self.eula_version})"


class AssetMaintenance(TaggableMixin, CloneableMixin, ExportableMixin,
                        JournalingMixin, ImageAttachmentMixin, FileAttachmentMixin,
                        SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

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
    title = models.CharField(max_length=200, default='Maintenance')
    description = models.TextField(blank=True)
    supplier = models.ForeignKey('assets.Supplier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Supplier/Vendor")
    performed_by = models.CharField(max_length=200, blank=True)
    maintenance_type = models.CharField(
        max_length=50,
        choices=MAINTENANCE_TYPE_CHOICES,
        default=MAINTENANCE_TYPE_REPAIR,
        verbose_name="Maintenance Type",
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
        verbose_name="Maintenance Cost"
    )
    start_date = models.DateField(verbose_name="Start Date", db_index=True)
    completion_date = models.DateField(null=True, blank=True, verbose_name="Completion Date", db_index=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_maintenances', blank=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = _("Asset Maintenance")
        verbose_name_plural = _("Asset Maintenances")

    def __str__(self):
        return f"{self.get_maintenance_type_display()} on {self.asset.name}"

    def get_absolute_url(self):
        return reverse('compliance:assetmaintenance_detail', kwargs={'pk': self.pk})

    @property
    def downtime_days(self):
        if self.start_date and self.completion_date:
            return (self.completion_date - self.start_date).days
        return None
