from django.db import models
from django.conf import settings # Required for referencing AUTH_USER_MODEL
from django.utils.text import slugify # Needed for slug generation if we automate it later
from django.db.models import Q, CheckConstraint, F # Import Q and CheckConstraint
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils import timezone # Added import for timezone default fields
from software.models import Software # Import Software model
from core.models import BaseModel, ChangeLoggingMixin # Added import
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.auth import get_user_model

User = get_user_model()


# Create your models here.

class StatusLabel(ChangeLoggingMixin, BaseModel):
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
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default=TYPE_DEPLOYABLE)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")

    class Meta:
        ordering = ['name']
        verbose_name = "Status Label"
        verbose_name_plural = "Status Labels"

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:statuslabel_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            base_slug = self.slug
            counter = 1
            while StatusLabel.objects.filter(slug=self.slug).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

class AssetRole(ChangeLoggingMixin, BaseModel):
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

class Manufacturer(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)  # Re-add unique=True
    description = models.TextField(blank=True, null=True)
    
    contacts = GenericRelation('organization.ContactAssignment')

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


class CustomField(ChangeLoggingMixin, BaseModel):
    FIELD_TYPE_TEXT = 'text'
    FIELD_TYPE_NUMBER = 'number'
    FIELD_TYPE_DATE = 'date'
    FIELD_TYPE_BOOLEAN = 'boolean'
    FIELD_TYPE_SELECT = 'select'
    FIELD_TYPE_CHOICES = [
        (FIELD_TYPE_TEXT, 'Text'),
        (FIELD_TYPE_NUMBER, 'Number'),
        (FIELD_TYPE_DATE, 'Date'),
        (FIELD_TYPE_BOOLEAN, 'Boolean'),
        (FIELD_TYPE_SELECT, 'Select / Dropdown'),
    ]

    name = models.SlugField(max_length=50, unique=True, verbose_name="Field Name", help_text="Slug-like name (e.g. sim_card_number)")
    label = models.CharField(max_length=100, verbose_name="Display Label")
    field_type = models.CharField(max_length=50, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_TEXT, verbose_name="Field Type")
    choices = models.TextField(blank=True, null=True, help_text="New-line separated list of choices (only for 'select' type)")
    required = models.BooleanField(default=False, verbose_name="Required")

    class Meta:
        ordering = ['label']
        verbose_name = "Custom Field"
        verbose_name_plural = "Custom Fields"

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:customfield_detail', kwargs={'pk': self.pk})


class CustomFieldset(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Fieldset Name")
    fields = models.ManyToManyField(CustomField, related_name='fieldsets', blank=True, verbose_name="Custom Fields")

    class Meta:
        ordering = ['name']
        verbose_name = "Custom Fieldset"
        verbose_name_plural = "Custom Fieldsets"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:customfieldset_detail', kwargs={'pk': self.pk})


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


class AssetType(ChangeLoggingMixin, BaseModel):
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
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="Manufacturer part number or SKU")

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

    # Other
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="asset_types", blank=True)

    class Meta:
        # ordering = ['manufacturer', 'model'] # Removed old ordering
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

class Asset(ChangeLoggingMixin, BaseModel):
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
    warranty_expiration = models.DateField(blank=True, null=True)
    
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
    supplier = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Supplier"
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

class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('created_at', 'Created'),
        ('updated_at', 'Updated'),
        ('checked_out', 'Checked Out'),
        ('checked_in', 'Checked In'),
        ('audited', 'Audited'),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='logs', db_index=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp'] # Show newest logs first

    def __str__(self):
        return f"{self.asset} - {self.get_action_display()} by {self.user or 'System'} on {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

class InstalledSoftware(BaseModel):
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


class ComponentType(ChangeLoggingMixin, BaseModel):
    """Catalog of physical hardware component models (e.g. Samsung 990 Pro 2TB SSD)."""
    CATEGORY_RAM = 'ram'
    CATEGORY_STORAGE = 'storage'
    CATEGORY_GPU = 'gpu'
    CATEGORY_CPU = 'cpu'
    CATEGORY_NIC = 'nic'
    CATEGORY_OTHER = 'other'
    
    CATEGORY_CHOICES = [
        (CATEGORY_RAM, 'Memory (RAM)'),
        (CATEGORY_STORAGE, 'Storage (SSD/HDD)'),
        (CATEGORY_GPU, 'Graphics Card (GPU)'),
        (CATEGORY_CPU, 'Processor (CPU)'),
        (CATEGORY_NIC, 'Network Card (NIC)'),
        (CATEGORY_OTHER, 'Other'),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='component_types')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or part number")
    specs = models.CharField(max_length=255, blank=True, help_text="Specific capacity/speed details (e.g. 16GB DDR5 5600MHz)")
    description = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='component_types', blank=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Component Type"
        verbose_name_plural = "Component Types"

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('assets:componenttype_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.manufacturer.name}-{self.name}")
            base_slug = self.slug
            counter = 1
            while ComponentType.objects.filter(slug=self.slug).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class ComponentInstance(ChangeLoggingMixin, BaseModel):
    """A physical component unit (e.g., a specific NVMe SSD with serial number) installed inside an Asset."""
    STATUS_INSTALLED = 'installed'
    STATUS_IN_STOCK = 'in_stock'
    STATUS_DEFECTIVE = 'defective'
    
    STATUS_CHOICES = [
        (STATUS_INSTALLED, 'Installed'),
        (STATUS_IN_STOCK, 'In Stock'),
        (STATUS_DEFECTIVE, 'Defective'),
    ]

    component_type = models.ForeignKey(ComponentType, on_delete=models.PROTECT, related_name='instances')
    serial_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="Physical serial number of the part")
    parent_asset = models.ForeignKey(Asset, on_delete=models.SET_NULL, null=True, blank=True, related_name='components', db_index=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_IN_STOCK)
    purchase_date = models.DateField(blank=True, null=True)
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='component_instances', blank=True)

    class Meta:
        ordering = ('component_type', 'serial_number')
        verbose_name = "Component"
        verbose_name_plural = "Components"

    def __str__(self):
        serial_part = f" [S/N: {self.serial_number}]" if self.serial_number else ""
        return f"{self.component_type.manufacturer.name} {self.component_type.name}{serial_part}"

    def get_absolute_url(self):
        return reverse('assets:componentinstance_detail', kwargs={'pk': self.pk})


class Accessory(ChangeLoggingMixin, BaseModel):
    """Bulk non-serialized returnable peripherals tracked in inventory (e.g. Dell Keyboard)."""
    CATEGORY_KEYBOARD = 'keyboard'
    CATEGORY_MOUSE = 'mouse'
    CATEGORY_CHARGER = 'charger'
    CATEGORY_ADAPTOR = 'adaptor'
    CATEGORY_DISPLAY = 'display'
    CATEGORY_CABLE = 'cable'
    CATEGORY_OTHER = 'other'

    CATEGORY_CHOICES = [
        (CATEGORY_KEYBOARD, 'Keyboard'),
        (CATEGORY_MOUSE, 'Mouse'),
        (CATEGORY_CHARGER, 'Charger'),
        (CATEGORY_ADAPTOR, 'Adapter/Dongle'),
        (CATEGORY_DISPLAY, 'Display/Monitor'),
        (CATEGORY_CABLE, 'Cable'),
        (CATEGORY_OTHER, 'Other'),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='accessories')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or manufacturer part number")
    qty = models.PositiveIntegerField(default=0, verbose_name="Total Stock")
    min_qty = models.PositiveIntegerField(default=0, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
    allow_overallocate = models.BooleanField(default=False, verbose_name="Allow Over-allocation", help_text="Allow checkout count to exceed stock capacity")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='accessories', blank=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='accessories', db_index=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Accessory"
        verbose_name_plural = "Accessories"

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('assets:accessory_detail', kwargs={'pk': self.pk})

    @property
    def checked_out_qty(self):
        # Calculate active assignments total quantity
        return sum(assignment.qty for assignment in self.assignments.all())

    @property
    def remaining_qty(self):
        return self.qty - self.checked_out_qty

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.manufacturer.name}-{self.name}")
            base_slug = self.slug
            counter = 1
            while Accessory.objects.filter(slug=self.slug).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class AccessoryAssignment(ChangeLoggingMixin, BaseModel):
    """Checkout allocation mapping for physical accessories assigned to users or locations."""
    accessory = models.ForeignKey(Accessory, on_delete=models.CASCADE, related_name='assignments', db_index=True)
    assigned_holder = models.ForeignKey('organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessory_assignments', db_index=True)
    assigned_location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessory_assignments', db_index=True)
    qty = models.PositiveIntegerField(default=1, verbose_name="Checkout Quantity")
    assigned_date = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-assigned_date',)
        verbose_name = "Accessory Assignment"
        verbose_name_plural = "Accessory Assignments"
        constraints = [
            CheckConstraint(
                check=(
                    Q(assigned_holder__isnull=False, assigned_location__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=False)
                ),
                name='chk_accessory_assignment_single_target'
            )
        ]

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or "Unknown"
        return f"{self.qty}x {self.accessory} assigned to {recipient}"


class Consumable(ChangeLoggingMixin, BaseModel):
    """Non-returnable bulk items that are permanently consumed (e.g. thermal paste, printer toner)."""
    CATEGORY_TONER = 'toner'
    CATEGORY_INK = 'ink'
    CATEGORY_BATTERIES = 'batteries'
    CATEGORY_THERMAL_PASTE = 'thermal_paste'
    CATEGORY_PAPER = 'paper'
    CATEGORY_OTHER = 'other'

    CATEGORY_CHOICES = [
        (CATEGORY_TONER, 'Toner/Ink'),
        (CATEGORY_INK, 'Ink Cartridge'),
        (CATEGORY_BATTERIES, 'Batteries'),
        (CATEGORY_THERMAL_PASTE, 'Thermal Paste'),
        (CATEGORY_PAPER, 'Printer Paper'),
        (CATEGORY_OTHER, 'Other'),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='consumables')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or manufacturer part number")
    qty = models.PositiveIntegerField(default=0, verbose_name="Total Quantity")
    min_qty = models.PositiveIntegerField(default=0, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
    allow_overallocate = models.BooleanField(default=False, verbose_name="Allow Over-allocation", help_text="Allow consumption count to exceed stock capacity")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='consumables', blank=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='consumables', db_index=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Consumable"
        verbose_name_plural = "Consumables"

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('assets:consumable_detail', kwargs={'pk': self.pk})

    @property
    def consumed_qty(self):
        return sum(consumption.qty for consumption in self.consumptions.all())

    @property
    def remaining_qty(self):
        return self.qty - self.consumed_qty

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.manufacturer.name}-{self.name}")
            base_slug = self.slug
            counter = 1
            while Consumable.objects.filter(slug=self.slug).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class ConsumableAssignment(ChangeLoggingMixin, BaseModel):
    """Permanent consumption payout record mapping for bulk consumables debited from stock."""
    consumable = models.ForeignKey(Consumable, on_delete=models.CASCADE, related_name='consumptions', db_index=True)
    assigned_holder = models.ForeignKey('organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='consumable_consumptions', db_index=True)
    assigned_location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='consumable_consumptions', db_index=True)
    qty = models.PositiveIntegerField(default=1, verbose_name="Consumed Quantity")
    assigned_date = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-assigned_date',)
        verbose_name = "Consumable Consumption"
        verbose_name_plural = "Consumable Consumptions"
        constraints = [
            CheckConstraint(
                check=(
                    Q(assigned_holder__isnull=False, assigned_location__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=False)
                ),
                name='chk_consumable_assignment_single_target'
            )
        ]

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or "Unknown"
        return f"{self.qty}x {self.consumable} consumed by {recipient}"


class CustodyReceipt(ChangeLoggingMixin, BaseModel):
    """Immutable digital custody sign-off ledger receipt binding checked-out assets to Asset Holders."""
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='custody_receipts', db_index=True)
    holder = models.ForeignKey('organization.AssetHolder', on_delete=models.CASCADE, related_name='custody_receipts')
    verification_hash = models.CharField(max_length=64, unique=True)
    signature_canvas = models.TextField(help_text="Base64 canvas stroke vector string representation")
    signed_at = models.DateTimeField(default=timezone.now)
    eula_version = models.CharField(max_length=10, default='1.0')

    class Meta:
        ordering = ('-signed_at',)
        verbose_name = "Custody Receipt"
        verbose_name_plural = "Custody Receipts"

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

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='maintenances', db_index=True)
    supplier = models.CharField(max_length=100, blank=True, verbose_name="Supplier/Vendor")
    maintenance_type = models.CharField(
        max_length=50,
        choices=MAINTENANCE_TYPE_CHOICES,
        default=MAINTENANCE_TYPE_REPAIR,
        verbose_name="Maintenance Type"
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Maintenance Cost"
    )
    start_date = models.DateField(verbose_name="Start Date")
    completion_date = models.DateField(null=True, blank=True, verbose_name="Completion Date")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = "Asset Maintenance"
        verbose_name_plural = "Asset Maintenances"

    def __str__(self):
        return f"{self.get_maintenance_type_display()} on {self.asset.name}"

    def get_absolute_url(self):
        return reverse('assets:assetmaintenance_detail', kwargs={'pk': self.pk})

    @property
    def downtime_days(self):
        if self.start_date and self.completion_date:
            return (self.completion_date - self.start_date).days
        return None


class Kit(ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Kit Name")
    description = models.TextField(blank=True, verbose_name="Description")
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='kits', db_index=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Kit"
        verbose_name_plural = "Kits"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:kit_detail', kwargs={'pk': self.pk})


class KitItem(ChangeLoggingMixin, BaseModel):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name='items', verbose_name="Kit", db_index=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Asset Type / Model")
    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Accessory Catalog Item")
    license = models.ForeignKey('licenses.License', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Software License")
    qty = models.PositiveIntegerField(default=1, verbose_name="Quantity", help_text="Quantity to checkout (only applies to Accessories)")

    class Meta:
        verbose_name = "Kit Item"
        verbose_name_plural = "Kit Items"
        constraints = [
            CheckConstraint(
                check=(
                    Q(asset_type__isnull=False, accessory__isnull=True, license__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=False, license__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=False)
                ),
                name='chk_kit_item_single_target'
            )
        ]

    def __str__(self):
        if self.asset_type:
            return f"Asset Type: {self.asset_type}"
        elif self.accessory:
            return f"{self.qty}x Accessory: {self.accessory}"
        elif self.license:
            return f"License: {self.license.software.name} ({self.license.name})"
        return "Empty Kit Item"

    def clean(self):
        super().clean()
        targets = [self.asset_type, self.accessory, self.license]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, or License.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target (must be either Asset Type OR Accessory OR License).")
