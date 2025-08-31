from django.db import models
from django.conf import settings
from django.urls import reverse
from software.models import Software
from core.models import BaseModel, ChangeLoggingMixin, AssetBoxModel
from core.mixins import ExportableMixin, SoftDeleteMixin, CustomFieldDataMixin, JournalingMixin, TaggableMixin, AutoSlugMixin
from extras.models import CustomFieldset
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.auth import get_user_model
from compliance.models import generate_token

User = get_user_model()


from core.managers import SoftDeleteManager, AllObjectsManager


class StatusLabel(AutoSlugMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    TYPE_DEPLOYABLE = 'deployable'
    TYPE_PENDING = 'pending'
    TYPE_UNDEPLOYABLE = 'undeployable'
    TYPE_ARCHIVED = 'archived'
    TYPE_CHOICES = [
        (TYPE_DEPLOYABLE, 'Deployable'),
        (TYPE_PENDING, 'Pending'),
        (TYPE_UNDEPLOYABLE, 'Undeployable'),
        (TYPE_ARCHIVED, 'Archived'),
    ]

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default=TYPE_DEPLOYABLE, db_index=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    tags = models.ManyToManyField('extras.Tag', related_name='status_labels_tagged', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Status Label"
        verbose_name_plural = "Status Labels"

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:statuslabel_detail', kwargs={'pk': self.pk})



class AssetRole(TaggableMixin, ChangeLoggingMixin, BaseModel):
    """Categorizes assets based on their functional role (e.g., Laptop, Monitor, Server)."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # Add new fields
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='asset_roles',
        blank=True
    )

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse('assets:assetrole_detail', args=[self.pk])

class Manufacturer(ExportableMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)  # Re-add unique=True
    description = models.TextField(blank=True, null=True)
    
    contacts = GenericRelation('organization.ContactAssignment')
    tags = models.ManyToManyField('extras.Tag', related_name='manufacturers', blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:manufacturer_detail', kwargs={'pk': self.pk})

    @property
    def get_support_contact(self):
        """Resolves the active support contact assignment dynamically."""
        # 1. Search for a Contact assignment with role slug 'support' or 'technical-support'
        assignment = self.contacts.filter(role__slug__in=['support', 'technical-support']).first()
        if not assignment:
            # 2. Fallback to any assignment with 'primary' priority
            assignment = self.contacts.filter(priority='primary').first()
        if not assignment:
            # 3. Fallback to any contact assignment
            assignment = self.contacts.first()
        
        return assignment.contact if assignment else None

class Depreciation(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Depreciation Name")
    months = models.PositiveIntegerField(verbose_name="Lifespan (Months)", help_text="Useful lifespan in months for straight-line calculations")

    class Meta:
        ordering = ['name']
        verbose_name = "Depreciation"
        verbose_name_plural = "Depreciations"

    def __str__(self):
        return f"{self.name} ({self.months} months)"

    def get_absolute_url(self):
        return reverse('assets:depreciation_detail', kwargs={'pk': self.pk})


class AssetType(AutoSlugMixin, AssetBoxModel):
    """Defines a specific type of asset (e.g., a specific laptop model)."""
    slug_source = ('manufacturer__name', 'model')
    
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
    model = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255, unique=True)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="Manufacturer part number or SKU")

    # Specs
    cpu = models.CharField(max_length=100, blank=True, db_index=True, verbose_name="Processor (CPU)")
    ram_gb = models.PositiveIntegerField(blank=True, null=True, verbose_name="RAM (GB)")
    storage_capacity_gb = models.PositiveIntegerField(blank=True, null=True, verbose_name="Storage (GB)")
    storage_type = models.CharField(
        max_length=10,
        choices=STORAGE_TYPE_CHOICES,
        blank=True,
        verbose_name="Storage Type"
    )
    gpu = models.CharField(max_length=100, blank=True, verbose_name="Graphics (GPU)")
    eol_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="EOL (Months)",
        help_text="Lifespan in months before EOL replacement"
    )
    custom_fieldset = models.ForeignKey(
        CustomFieldset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name="Custom Fieldset"
    )
    depreciation = models.ForeignKey(
        Depreciation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name="Depreciation"
    )

    category = models.ForeignKey(
        'Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name="Category",
        db_index=True
    )
    image = models.ImageField(upload_to='asset_types/', blank=True, null=True, verbose_name="Model Image")

    # Other
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="asset_types", blank=True)
    requestable = models.BooleanField(default=False, db_index=True, help_text="Allow users to request assets of this type")

    class Meta:
        unique_together = ('manufacturer', 'model')
        verbose_name = "Asset Type"
        verbose_name_plural = "Asset Types"

    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse('assets:assettype_detail', kwargs={'pk': self.pk})



class Asset(CustomFieldDataMixin, SoftDeleteMixin, AssetBoxModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    
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
    serial_number = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.PROTECT, related_name='assets', null=True, blank=True, db_index=True)
    asset_role = models.ForeignKey(AssetRole, on_delete=models.SET_NULL, blank=True, null=True, db_index=True)
    purchase_date = models.DateField(blank=True, null=True, db_index=True)
    warranty_expiration = models.DateField(blank=True, null=True, db_index=True)
    
    # Procurement Metadata (Maturity Phase 1)
    purchase_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Purchase Cost"
    )
    order_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Order Number"
    )
    supplier = models.ForeignKey(
        'Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name="Supplier",
        db_index=True
    )
    salvage_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Salvage Value"
    )
    status = models.ForeignKey(StatusLabel, on_delete=models.PROTECT, related_name='assets', null=True, blank=True, db_index=True)
    location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='assets', db_index=True)
    notes = models.TextField(blank=True, null=True)
    tags = models.ManyToManyField('extras.Tag', related_name="assets", blank=True)
    last_audited = models.DateTimeField(null=True, blank=True, verbose_name="Last Audited", db_index=True)
    last_audited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audited_assets',
        verbose_name="Last Audited By"
    )
    custom_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Custom Values"
    )
    requestable = models.BooleanField(default=False, db_index=True, help_text="Allow users to request this asset")

    @property
    def manufacturer(self):
        return self.asset_type.manufacturer if self.asset_type else None

    @property
    def model(self):
        return self.asset_type.model if self.asset_type else None

    def get_status_display(self):
        return self.status.name if self.status else "—"

    @property
    def eol_date(self):
        if self.purchase_date and self.asset_type and self.asset_type.eol_months:
            import datetime
            year = self.purchase_date.year
            month = self.purchase_date.month
            day = self.purchase_date.day
            
            # Add eol_months
            total_months = month + self.asset_type.eol_months - 1
            new_year = year + total_months // 12
            new_month = total_months % 12 + 1
            
            # Handle month end day overflows (e.g. Feb 30th -> Feb 28th)
            try:
                return datetime.date(new_year, new_month, day)
            except ValueError:
                # Get the last day of that new month
                if new_month == 12:
                    next_month_first = datetime.date(new_year + 1, 1, 1)
                else:
                    next_month_first = datetime.date(new_year, new_month + 1, 1)
                return next_month_first - datetime.timedelta(days=1)
        return None

    @property
    def time_to_eol(self):
        eol = self.eol_date
        if eol:
            import datetime
            today = datetime.date.today()
            if today >= eol:
                return "Expired"
            
            # Calculate simple difference
            years = eol.year - today.year
            months = eol.month - today.month
            if eol.day < today.day:
                months -= 1
            if months < 0:
                years -= 1
                months += 12
                
            parts = []
            if years > 0:
                parts.append(f"{years} year{'s' if years != 1 else ''}")
            if months > 0:
                parts.append(f"{months} month{'s' if months != 1 else ''}")
            return ", ".join(parts) or "Less than a month"
        return "—"

    @property
    def total_cost_of_ownership(self):
        from decimal import Decimal
        cost = self.purchase_cost or Decimal('0.00')
        maintenance_cost = sum(m.cost or Decimal('0.00') for m in self.maintenances.all())
        return cost + maintenance_cost

    @property
    def current_value(self):
        """Calculates straight-line depreciation value on a monthly basis."""
        from decimal import Decimal
        import datetime

        if not self.purchase_cost:
            return None

        # If no depreciation scheme, value is original cost
        if not self.asset_type or not self.asset_type.depreciation:
            return self.purchase_cost

        deprec = self.asset_type.depreciation
        if deprec.months <= 0:
            return self.purchase_cost

        if not self.purchase_date:
            return self.purchase_cost

        # Calculate exact month difference
        today = datetime.date.today()
        months_held = (today.year - self.purchase_date.year) * 12 + today.month - self.purchase_date.month
        # If purchase date is in future, or same month, zero months held
        if months_held <= 0:
            return self.purchase_cost

        # If held longer than the depreciation schedule, value is salvage value
        salvage = self.salvage_value or Decimal('0.00')
        if months_held >= deprec.months:
            return salvage

        # Straight-line depreciation math
        depreciable_base = self.purchase_cost - salvage
        monthly_depreciation = depreciable_base / Decimal(str(deprec.months))
        current = self.purchase_cost - (monthly_depreciation * Decimal(str(months_held)))
        
        return max(current, salvage)

    @property
    def is_modular(self):
        if self.components.exists():
            return True
        if self.asset_role:
            role_slug = self.asset_role.slug.lower()
            return 'server' in role_slug or 'modular' in role_slug or 'workstation' in role_slug or 'hypervisor' in role_slug
        return False

    def __str__(self):
        return f"{self.name} ({self.asset_tag})"

    def get_absolute_url(self):
        """Return the canonical URL for the asset."""
        return reverse('assets:asset_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        if not self.asset_tag:
            sequence, _ = AssetTagSequence.objects.get_or_create(
                prefix='ASSET-',
                defaults={'next_value': 1, 'zero_padding': 6}
            )
            self.asset_tag = sequence.next_tag()
        super().save(*args, **kwargs)

class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('created_at', 'Created'),
        ('updated_at', 'Updated'),
        ('checked_out', 'Checked Out'),
        ('checked_in', 'Checked In'),
        ('audited', 'Audited'),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='logs', db_index=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp'] # Show newest logs first

    def __str__(self):
        return f"{self.asset} - {self.get_action_display()} by {self.user or 'System'} on {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

class InstalledSoftware(ChangeLoggingMixin, BaseModel):
    """
    Represents an instance of software discovered or inventoried on a specific asset.
    Distinct from license assignment/tracking.
    """
    asset = models.ForeignKey(
        to=Asset,
        on_delete=models.CASCADE, # If Asset is deleted, remove its inventory
        related_name='installed_software',
        db_index=True
    )
    software = models.ForeignKey(
        to=Software,
        on_delete=models.PROTECT, # Don't delete Software catalog item if installed instance exists
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
        unique_together = ('asset', 'software', 'version_detected') # Allow tracking same sw multiple times if version changes
        ordering = ['asset', 'software', '-last_seen_date']
        verbose_name = "Installed Software Instance"
        verbose_name_plural = "Installed Software Instances"

    def __str__(self):
        version_part = f" (v{self.version_detected})" if self.version_detected else ""
        return f"{self.software.name}{version_part} on {self.asset.name}"

    def get_absolute_url(self):
        # Likely won't have its own detail view, link back to the asset
        return self.asset.get_absolute_url()


class Supplier(AutoSlugMixin, AssetBoxModel):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    website = models.URLField(max_length=500, blank=True, null=True)
    contact_email = models.EmailField(max_length=255, blank=True, null=True)
    contact_phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    contact_name = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    tags = models.ManyToManyField('extras.Tag', related_name='suppliers', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Supplier"
        verbose_name_plural = "Suppliers"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:supplier_detail', kwargs={'pk': self.pk})




class Category(AutoSlugMixin, AssetBoxModel):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    color = models.CharField(max_length=6, blank=True, null=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    description = models.TextField(blank=True, null=True)
    email_on_checkout = models.BooleanField(default=False, help_text="Send email notification on asset checkout")
    email_on_checkin = models.BooleanField(default=False, help_text="Send email notification on asset checkin")
    require_acceptance = models.BooleanField(default=False, help_text="Require digital acceptance on checkout")
    email_eula = models.BooleanField(default=False, help_text="Send EULA email on acceptance")
    applies_to = models.JSONField(default=list, blank=True, help_text="Applies to: ['asset', 'accessory', 'license']")
    tags = models.ManyToManyField('extras.Tag', related_name='categories', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:category_detail', kwargs={'pk': self.pk})




class AssetRequest(JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED = 'denied'
    STATUS_FULFILLED = 'fulfilled'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_DENIED, 'Denied'),
        (STATUS_FULFILLED, 'Fulfilled'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name='asset_requests', db_index=True)
    asset = models.ForeignKey('Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    request_date = models.DateTimeField(auto_now_add=True, db_index=True)
    response_date = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_request_responses')
    notes = models.TextField(blank=True, null=True)
    response_notes = models.TextField(blank=True, null=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_requests_tagged', blank=True)

    class Meta:
        ordering = ['-request_date']
        verbose_name = "Asset Request"
        verbose_name_plural = "Asset Requests"

    def __str__(self):
        target = str(self.asset) if self.asset else str(self.asset_type) if self.asset_type else "Any Asset"
        return f"Request for {target} by {self.requester} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('assets:assetrequest_detail', kwargs={'pk': self.pk})


class AssetTagSequence(ChangeLoggingMixin, BaseModel):
    prefix = models.CharField(max_length=20, default='ASSET-', unique=True)
    next_value = models.PositiveIntegerField(default=1)
    zero_padding = models.PositiveSmallIntegerField(default=6)

    class Meta:
        verbose_name = "Asset Tag Sequence"
        verbose_name_plural = "Asset Tag Sequences"

    def __str__(self):
        return f'{self.prefix} (next: {self.next_value:0{self.zero_padding}d})'

    def get_absolute_url(self):
        return reverse('assets:assettagsequence_detail', kwargs={'pk': self.pk})

    def next_tag(self):
        tag = f'{self.prefix}{self.next_value:0{self.zero_padding}d}'
        self.next_value += 1
        self.save(update_fields=['next_value'])
        return tag
