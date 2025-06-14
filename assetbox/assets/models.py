from django.db import models
from django.conf import settings # Required for referencing AUTH_USER_MODEL
from django.utils.text import slugify # Needed for slug generation if we automate it later
from django.db.models import Q, CheckConstraint, F # Import Q and CheckConstraint
from django.urls import reverse

# Create your models here.

class AssetRole(models.Model):
    """Categorizes assets based on their functional role (e.g., Laptop, Monitor, Server)."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # Add new fields
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    config_template = models.ForeignKey(
        to='extras.ConfigTemplate',
        on_delete=models.SET_NULL,
        related_name='asset_roles',
        blank=True,
        null=True
    )
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='asset_roles',
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse('assets:assetrole_detail', args=[self.pk])

    def save(self, *args, **kwargs):
        # Auto-generate slug if it's empty
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Asset Role"
        verbose_name_plural = "Asset Roles"
        ordering = ('name',)

# Location model was moved to organization app

class Manufacturer(models.Model):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)  # Re-add unique=True
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:manufacturer_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        # Auto-generate slug if it's empty
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

# Region, SiteGroup, Tenant, Tag, Site models were moved to organization app

class AssetType(models.Model):
    """Defines a specific type of asset (e.g., a specific laptop model)."""
    STORAGE_SSD = 'ssd'
    STORAGE_NVME = 'nvme'
    STORAGE_HDD = 'hdd'
    STORAGE_EMMC = 'emmc'
    STORAGE_TYPE_CHOICES = [
        (STORAGE_SSD, 'SSD'),
        (STORAGE_NVME, 'NVMe SSD'),
        (STORAGE_HDD, 'HDD'),
        (STORAGE_EMMC, 'eMMC'),
        ('', 'Other/Unknown') # Allow blank choice
    ]

    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='asset_types')
    model = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    part_number = models.CharField(max_length=100, blank=True, help_text="Manufacturer part number or SKU")

    # Specs
    cpu = models.CharField(max_length=100, blank=True, verbose_name="Processor (CPU)")
    ram_gb = models.PositiveIntegerField(blank=True, null=True, verbose_name="RAM (GB)")
    storage_capacity_gb = models.PositiveIntegerField(blank=True, null=True, verbose_name="Storage (GB)")
    storage_type = models.CharField(
        max_length=10,
        choices=STORAGE_TYPE_CHOICES,
        blank=True,
        verbose_name="Storage Type"
    )
    gpu = models.CharField(max_length=100, blank=True, verbose_name="Graphics (GPU)")

    # Other
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="asset_types", blank=True)

    # Standard fields (auto-managed by Django)
    created = models.DateTimeField(auto_now_add=True, editable=False)
    last_updated = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        ordering = ['manufacturer', 'model']
        unique_together = ('manufacturer', 'model') # Ensure model is unique per manufacturer
        verbose_name = "Asset Type"
        verbose_name_plural = "Asset Types"

    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse('assets:assettype_detail', kwargs={'slug': self.slug})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.manufacturer.name}-{self.model}")
            # Ensure slug uniqueness if auto-generated
            base_slug = self.slug
            counter = 1
            while AssetType.objects.filter(slug=self.slug).exists():
                 self.slug = f"{base_slug}-{counter}"
                 counter += 1
        super().save(*args, **kwargs)

class Asset(models.Model):
    # --- Define choices as class attributes --- 
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
    # --- End Choices ---

    name = models.CharField(max_length=255)
    asset_tag = models.CharField(max_length=50, unique=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.PROTECT, related_name='assets', null=True, blank=True)
    asset_role = models.ForeignKey(AssetRole, on_delete=models.SET_NULL, blank=True, null=True)
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
    tags = models.ManyToManyField('extras.Tag', related_name="assets", blank=True)

    @property
    def manufacturer(self):
        return self.asset_type.manufacturer if self.asset_type else None

    @property
    def model(self):
        return self.asset_type.model if self.asset_type else None

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
        # Add other actions like 'audited', 'repaired', etc. later
    ]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp'] # Show newest logs first

    def __str__(self):
        return f"{self.asset} - {self.get_action_display()} by {self.user or 'System'} on {self.timestamp.strftime('%Y-%m-%d %H:%M')}"
