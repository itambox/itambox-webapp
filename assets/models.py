from django.db import models
from django.urls import reverse
from assetbox.core.models import BaseAssetModel, TaggableModel # Absolute import
# from django.contrib.auth.models import User # Use get_user_model
from django.contrib.auth import get_user_model
from assetbox.organization.models import Site, Location # Absolute import

User = get_user_model()

# Renamed from Category to AssetRole
class AssetRole(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asset Role"
        verbose_name_plural = "Asset Roles"

    def __str__(self):
        return self.name

    # Assuming get_absolute_url was added during previous rename attempts
    # def get_absolute_url(self):
    #     return reverse('assets:assetrole_detail', kwargs={'pk': self.pk})

class Manufacturer(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:manufacturer_detail', kwargs={'pk': self.pk})

class Asset(models.Model):
    STATUS_IN_USE = 'in_use'
    STATUS_AVAILABLE = 'available'
    STATUS_PENDING_REPAIR = 'pending_repair'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_IN_USE, 'In Use'),
        (STATUS_AVAILABLE, 'Available'),
        (STATUS_PENDING_REPAIR, 'Pending Repair'),
        (STATUS_RETIRED, 'Retired'),
    ]
    name = models.CharField(max_length=255)
    asset_tag = models.CharField(max_length=50, unique=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    asset_role = models.ForeignKey(AssetRole, on_delete=models.SET_NULL, blank=True, null=True)
    model = models.CharField(max_length=255)
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='assets')
    purchase_date = models.DateField(blank=True, null=True)
    warranty_expiration = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_AVAILABLE,
    )
    location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets')
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.asset_tag})"

    def get_absolute_url(self):
        """Return the canonical URL for the asset."""
        return reverse('assets:asset_detail', kwargs={'pk': self.pk})

    class Meta:
        pass

class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('checked_out', 'Checked Out'),
        ('checked_in', 'Checked In'),
        ('status_changed', 'Status Changed'), # Example: for repair, missing etc.
        ('archived', 'Archived'),
        ('deleted', 'Deleted'), # Maybe log deletion before actual delete?
    ]
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True) # Who performed the action
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-timestamp',)

    def __str__(self):
        return f"{self.asset} - {self.action} by {self.user or 'System'} at {self.timestamp}" 