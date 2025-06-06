from django.db import models
from django.urls import reverse
from assetbox.core.models import BaseAssetModel, TaggableModel # Absolute import
# from django.contrib.auth.models import User # Use get_user_model
from django.contrib.auth import get_user_model
from assetbox.organization.models import Site, Location # Absolute import

User = get_user_model()

# Renamed from Category to AssetRole
class AssetRole(TaggableModel, BaseAssetModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ('name',)
        verbose_name = "Asset Role"
        verbose_name_plural = "Asset Roles"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Update URL name
        return reverse('assets:assetrole_detail', kwargs={'pk': self.pk})

class Manufacturer(TaggableModel, BaseAssetModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:manufacturer_detail', kwargs={'pk': self.pk})

class Asset(TaggableModel, BaseAssetModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('in_use', 'In Use'),
        ('in_repair', 'In Repair'),
        ('missing', 'Missing'),
        ('archived', 'Archived'),
        ('pending_disposal', 'Pending Disposal'),
    ]
    name = models.CharField(max_length=100)
    asset_tag = models.CharField(max_length=50, unique=True, blank=True, null=True)
    serial_number = models.CharField(max_length=100, unique=True, blank=True, null=True)
    # Changed from category to asset_role
    asset_role = models.ForeignKey(AssetRole, on_delete=models.PROTECT, related_name='assets')
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='assets')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, blank=True, null=True, related_name='assets')
    purchase_date = models.DateField(blank=True, null=True)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    warranty_end_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('name',)
        # Add constraints if needed, e.g., unique_together=[('name', 'asset_tag')] ?

    def __str__(self):
        return self.name or f"Asset {self.pk}"

    def get_absolute_url(self):
        return reverse('assets:asset_detail', kwargs={'pk': self.pk})

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