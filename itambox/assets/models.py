from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from software.models import Software
from core.models import BaseModel, ChangeLoggingMixin, StandardModel, DeletableVaultModel
from core.mixins import CustomFieldDataMixin, JournalingMixin, TaggableMixin, AutoSlugMixin, BookmarkableMixin, SubscribableMixin, SoftDeleteMixin
from extras.models import CustomFieldset
from django.contrib.contenttypes.fields import GenericRelation, GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from compliance.models import generate_token

User = get_user_model()


from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, TenantScopingManager, TenantScopingAllObjectsManager


class AssetStateMachine:
    ALLOWED_TRANSITIONS = {
        'pending': ['deployable', 'undeployable', 'archived'],
        'deployable': ['pending', 'undeployable', 'archived', 'deployed'],
        'deployed': ['deployable', 'undeployable', 'archived', 'pending'],
        'undeployable': ['pending', 'deployable', 'archived'],
        'archived': ['pending']
    }

    @staticmethod
    def validate_transition(current_status_type, new_status_type, is_checked_out):
        if current_status_type == new_status_type:
            return
        if new_status_type not in AssetStateMachine.ALLOWED_TRANSITIONS.get(current_status_type, []):
            raise ValidationError(f"Illegal state transition from {current_status_type} to {new_status_type}")
        if is_checked_out and new_status_type in ['undeployable', 'archived']:
            raise ValidationError("Cannot mark an actively checked-out asset as undeployable or archived. Check it in first.")


class StatusLabel(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    TYPE_DEPLOYABLE = 'deployable'
    TYPE_DEPLOYED = 'deployed'
    TYPE_PENDING = 'pending'
    TYPE_UNDEPLOYABLE = 'undeployable'
    TYPE_ARCHIVED = 'archived'
    TYPE_CHOICES = [
        (TYPE_DEPLOYABLE, 'Deployable'),
        (TYPE_DEPLOYED, 'Deployed'),
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
        verbose_name = _("Status Label")
        verbose_name_plural = _("Status Labels")

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:statuslabel_detail', kwargs={'pk': self.pk})



class AssetRole(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
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

    class Meta:
        verbose_name = _("Asset Role")
        verbose_name_plural = _("Asset Roles")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse('assets:assetrole_detail', args=[self.pk])

class Manufacturer(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)  # Re-add unique=True
    description = models.TextField(blank=True, null=True)
    
    contacts = GenericRelation('organization.ContactAssignment')
    tags = models.ManyToManyField('extras.Tag', related_name='manufacturers', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Manufacturer")
        verbose_name_plural = _("Manufacturers")

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

class Depreciation(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=100, unique=True, verbose_name="Depreciation Name")
    months = models.PositiveIntegerField(verbose_name="Lifespan (Months)", help_text="Useful lifespan in months for straight-line calculations")

    class Meta:
        ordering = ['name']
        verbose_name = _("Depreciation")
        verbose_name_plural = _("Depreciations")

    def __str__(self):
        return f"{self.name} ({self.months} months)"

    def get_absolute_url(self):
        return reverse('assets:depreciation_detail', kwargs={'pk': self.pk})


class AssetType(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Defines a specific type of asset (e.g., a specific laptop model)."""
    slug_source = ('manufacturer__name', 'model')
    

    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.PROTECT, related_name='asset_types')
    model = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255, unique=True)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="Manufacturer part number or SKU")

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
    custom_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Custom Values"
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
    asset_role = models.ForeignKey(
        'AssetRole',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_types',
        verbose_name="Asset Role",
        db_index=True
    )
    image = models.ImageField(upload_to='asset_types/', blank=True, null=True, verbose_name="Model Image")

    # Other
    description = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name="asset_types", blank=True)
    requestable = models.BooleanField(default=False, db_index=True, help_text="Allow users to request assets of this type")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['manufacturer', 'model'], name='unique_manufacturer_model')
        ]
        verbose_name = _("Asset Type")
        verbose_name_plural = _("Asset Types")


    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse('assets:assettype_detail', kwargs={'pk': self.pk})



class Asset(CustomFieldDataMixin, BookmarkableMixin, SubscribableMixin, DeletableVaultModel):
    objects = TenantScopingSoftDeleteManager()
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
    asset_tag = models.CharField(max_length=50, blank=True)
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
    current_book_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Materialized depreciation value"
    )
    depreciation_updated_at = models.DateTimeField(null=True, blank=True)
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
    purchase_order_line = models.ForeignKey('procurement.PurchaseOrderLine', on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True)
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
    requestable = models.BooleanField(null=True, blank=True, default=None, db_index=True, help_text="Allow users to request this asset")

    @property
    def is_requestable(self):
        if self.requestable is not None:
            return self.requestable
        return self.asset_type.requestable if self.asset_type else False

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
        if self.component_allocations.filter(deleted_at__isnull=True).exists():
            return True
        if self.asset_role:
            role_slug = self.asset_role.slug.lower()
            return 'server' in role_slug or 'modular' in role_slug or 'workstation' in role_slug or 'hypervisor' in role_slug
        return False

    @property
    def active_assignment(self):
        for assignment in self.assignments.all():
            if assignment.is_active:
                return assignment
        return None

    @property
    def assigned_to(self):
        active = self.active_assignment
        return active.assigned_target if active else None

    @property
    def category(self):
        return self.asset_type.category if self.asset_type else None

    class Meta:
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'asset_tag'],
                condition=models.Q(tenant__isnull=False),
                name='unique_tenant_asset_tag'
            ),
            models.UniqueConstraint(
                fields=['asset_tag'],
                condition=models.Q(tenant__isnull=True),
                name='unique_global_asset_tag'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.asset_tag})"

    def get_absolute_url(self):
        """Return the canonical URL for the asset."""
        return reverse('assets:asset_detail', kwargs={'pk': self.pk})

    def clean(self):
        super().clean()
        if self.pk and self.status_id:
            try:
                old_asset = Asset.objects.get(pk=self.pk)
                if old_asset.status_id and old_asset.status != self.status:
                    AssetStateMachine.validate_transition(
                        old_asset.status.type,
                        self.status.type,
                        self.assignments.filter(is_active=True).exists()
                    )
            except Asset.DoesNotExist:
                pass

    def save(self, *args, **kwargs):
        if not self.asset_tag:
            self.asset_tag = AssetTagSequence.get_next_tag_for_asset(self)
        else:
            # If the user accepted/used the next expected tag sequence, increment it atomically
            seq = AssetTagSequence.resolve_sequence_for_asset(self)
            if seq and self.asset_tag == seq.next_tag_preview:
                seq.next_tag()
        super().save(*args, **kwargs)



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
        # Likely won't have its own detail view, link back to the asset
        return self.asset.get_absolute_url()


class Supplier(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
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
        verbose_name = _("Supplier")
        verbose_name_plural = _("Suppliers")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:supplier_detail', kwargs={'pk': self.pk})




class Category(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    color = models.CharField(max_length=6, blank=True, null=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    description = models.TextField(blank=True, null=True)
    applies_to = models.JSONField(default=dict, blank=True, help_text="Applies to: {'asset': True, 'accessory': True, 'component': True}")
    tags = models.ManyToManyField('extras.Tag', related_name='categories', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:category_detail', kwargs={'pk': self.pk})




class AssetRequest(JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_PROCUREMENT = 'procurement'
    STATUS_DENIED = 'denied'
    STATUS_FULFILLED = 'fulfilled'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_PROCUREMENT, 'Awaiting Procurement'),
        (STATUS_DENIED, 'Denied'),
        (STATUS_FULFILLED, 'Fulfilled'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='asset_requests',
        db_index=True
    )
    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name='asset_requests', db_index=True)
    asset = models.ForeignKey('Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    component = models.ForeignKey('inventory.Component', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    accessory = models.ForeignKey('inventory.Accessory', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    consumable = models.ForeignKey('inventory.Consumable', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests', db_index=True)
    qty = models.PositiveIntegerField(default=1)
    source_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_requests',
        db_index=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    request_date = models.DateTimeField(auto_now_add=True, db_index=True)
    response_date = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_request_responses')
    
    # Intended assignee target fields (delegated targets)
    assigned_user = models.ForeignKey(
        'organization.AssetHolder',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='asset_requests'
    )
    assigned_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='asset_requests'
    )
    assigned_asset = models.ForeignKey(
        'Asset',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='child_requests_for'
    )
    
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sub_requests',
        db_index=True
    )
    is_group = models.BooleanField(default=False, db_index=True)

    notes = models.TextField(blank=True, null=True)
    response_notes = models.TextField(blank=True, null=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_requests_tagged', blank=True)

    @property
    def assigned_target(self):
        return self.assigned_user or self.assigned_location or self.assigned_asset

    @property
    def assigned_to(self):
        return self.assigned_target

    @property
    def assigned_to_type(self):
        if self.assigned_user: return 'assetholder'
        if self.assigned_location: return 'location'
        if self.assigned_asset: return 'asset'
        return None

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()

        if self.pk:
            try:
                old_status = AssetRequest.objects.get(pk=self.pk).status
                if old_status != self.status:
                    VALID_TRANSITIONS = {
                        AssetRequest.STATUS_PENDING: {AssetRequest.STATUS_APPROVED, AssetRequest.STATUS_DENIED, AssetRequest.STATUS_CANCELLED, AssetRequest.STATUS_FULFILLED},
                        AssetRequest.STATUS_APPROVED: {AssetRequest.STATUS_FULFILLED, AssetRequest.STATUS_CANCELLED, AssetRequest.STATUS_PROCUREMENT},
                        AssetRequest.STATUS_PROCUREMENT: {AssetRequest.STATUS_FULFILLED, AssetRequest.STATUS_CANCELLED, AssetRequest.STATUS_APPROVED},
                        AssetRequest.STATUS_DENIED: set(),
                        AssetRequest.STATUS_FULFILLED: set(),
                        AssetRequest.STATUS_CANCELLED: set(),
                    }
                    if self.status not in VALID_TRANSITIONS.get(old_status, set()):
                        raise ValidationError(f"Invalid state transition from {old_status} to {self.status}.")
            except AssetRequest.DoesNotExist:
                pass
        
        categories_filled = []
        if self.asset is not None or self.asset_type is not None:
            categories_filled.append("asset")
        if self.component is not None:
            categories_filled.append("component")
        if self.accessory is not None:
            categories_filled.append("accessory")
        if self.consumable is not None:
            categories_filled.append("consumable")
            
        if len(categories_filled) == 0:
            raise ValidationError("You must specify what item you are requesting (Asset, Asset Type, Component, Accessory, or Consumable).")
        if len(categories_filled) > 1:
            raise ValidationError("You cannot request more than one type of item in a single request.")
            
        if self.qty <= 0:
            raise ValidationError("Requested quantity must be greater than zero.")
            
        if not self.pk:
            if self.asset and not self.asset.is_requestable:
                raise ValidationError(f"The asset '{self.asset}' is not requestable.")
            if self.asset_type and not self.asset_type.requestable:
                raise ValidationError(f"The asset type '{self.asset_type}' is not requestable.")
            if self.asset and self.asset.status and self.asset.status.type != 'deployable':
                raise ValidationError(f"The asset '{self.asset}' is currently not available (Status: {self.asset.status.name}).")
            
            # Check for duplicate pending or approved requests by the same requester
            if self.requester_id and not getattr(self, '_skip_duplicate_check', False):
                duplicate_qs = AssetRequest.objects.filter(
                    requester_id=self.requester_id,
                    status__in=[AssetRequest.STATUS_PENDING, AssetRequest.STATUS_APPROVED],
                    assigned_user_id=self.assigned_user_id,
                    assigned_location_id=self.assigned_location_id,
                    assigned_asset_id=self.assigned_asset_id
                )
                if self.asset:
                    if duplicate_qs.filter(asset=self.asset).exists():
                        raise ValidationError(f"You already have a pending or approved request for the asset '{self.asset}'.")
                elif self.asset_type:
                    if duplicate_qs.filter(asset_type=self.asset_type, asset__isnull=True).exists():
                        raise ValidationError(f"You already have a pending or approved request for the asset type '{self.asset_type}'.")
                elif self.component:
                    if duplicate_qs.filter(component=self.component).exists():
                        raise ValidationError(f"You already have a pending or approved request for the component '{self.component}'.")
                elif self.accessory:
                    if duplicate_qs.filter(accessory=self.accessory).exists():
                        raise ValidationError(f"You already have a pending or approved request for the accessory '{self.accessory}'.")
                elif self.consumable:
                    if duplicate_qs.filter(consumable=self.consumable).exists():
                        raise ValidationError(f"You already have a pending or approved request for the consumable '{self.consumable}'.")
                        
        if self.asset and self.asset_type and self.asset.asset_type != self.asset_type:
            raise ValidationError("The selected asset does not match the requested asset type.")

    def save(self, *args, **kwargs):
        if not self.tenant:
            from core.managers import get_current_tenant
            self.tenant = get_current_tenant()
            
        # Auto-approval check for Accessories and Consumables
        if not self.pk and self.status == self.STATUS_PENDING:
            from django.conf import settings
            from django.utils import timezone
            from extras.models import ConfigContext
            
            # Default thresholds
            thresholds = getattr(settings, 'REQUISITION_AUTO_APPROVAL_THRESHOLDS', {
                'accessory': 3,
                'consumable': 5,
            })
            
            # Look up tenant config contexts for overrides
            if self.tenant:
                cc = ConfigContext.objects.filter(tenants=self.tenant).order_by('-weight').first()
                if cc and isinstance(cc.data, dict) and 'requisition_auto_approval_thresholds' in cc.data:
                    thresholds = cc.data['requisition_auto_approval_thresholds']
                    
            if self.accessory:
                max_qty = thresholds.get('accessory', 0)
                if self.qty <= max_qty and self.accessory.available >= self.qty:
                    self.status = self.STATUS_APPROVED
                    self.response_date = timezone.now()
                    self.response_notes = "Automatically approved based on available stock."
            elif self.consumable:
                max_qty = thresholds.get('consumable', 0)
                if self.qty <= max_qty and self.consumable.available >= self.qty:
                    self.status = self.STATUS_APPROVED
                    self.response_date = timezone.now()
                    self.response_notes = "Automatically approved based on available stock."
                    
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-request_date']
        verbose_name = _("Asset Request")
        verbose_name_plural = _("Asset Requests")
        permissions = [
            ("add_delegated_assetrequest", "Can request assets on behalf of others"),
            ("approve_assetrequest", "Can approve asset requests"),
            ("fulfill_assetrequest", "Can fulfill/claim asset requests"),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(assigned_user__isnull=True) & models.Q(assigned_location__isnull=True) & models.Q(assigned_asset__isnull=True)) |
                    (models.Q(assigned_user__isnull=False) & models.Q(assigned_location__isnull=True) & models.Q(assigned_asset__isnull=True)) |
                    (models.Q(assigned_user__isnull=True) & models.Q(assigned_location__isnull=False) & models.Q(assigned_asset__isnull=True)) |
                    (models.Q(assigned_user__isnull=True) & models.Q(assigned_location__isnull=True) & models.Q(assigned_asset__isnull=False))
                ),
                name='at_most_one_request_target'
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(component__isnull=True) & models.Q(accessory__isnull=True) & models.Q(consumable__isnull=True) & (models.Q(asset__isnull=False) | models.Q(asset_type__isnull=False))) |
                    (models.Q(asset__isnull=True) & models.Q(asset_type__isnull=True) & models.Q(component__isnull=False) & models.Q(accessory__isnull=True) & models.Q(consumable__isnull=True)) |
                    (models.Q(asset__isnull=True) & models.Q(asset_type__isnull=True) & models.Q(component__isnull=True) & models.Q(accessory__isnull=False) & models.Q(consumable__isnull=True)) |
                    (models.Q(asset__isnull=True) & models.Q(asset_type__isnull=True) & models.Q(component__isnull=True) & models.Q(accessory__isnull=True) & models.Q(consumable__isnull=False))
                ),
                name='exactly_one_requested_category'
            )
        ]

    def __str__(self):
        if self.asset:
            target = str(self.asset)
        elif self.asset_type:
            target = str(self.asset_type)
        elif self.component:
            target = f"{self.qty}x Component: {self.component}"
        elif self.accessory:
            target = f"{self.qty}x Accessory: {self.accessory}"
        elif self.consumable:
            target = f"{self.qty}x Consumable: {self.consumable}"
        else:
            target = "Any Asset"
        return f"Request for {target} by {self.requester} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('assets:assetrequest_detail', kwargs={'pk': self.pk})

    @property
    def unallocated_count(self):
        if self.is_group:
            return self.sub_requests.filter(
                asset__isnull=True, 
                component__isnull=True, 
                accessory__isnull=True, 
                consumable__isnull=True
            ).count()
        return 1 if not (self.asset or self.component or self.accessory or self.consumable) else 0



class AssetTagSequence(ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='tag_sequences',
        db_index=True,
        help_text="The tenant owning this sequence. Null represents system-wide/global sequences."
    )
    category = models.ForeignKey(
        'Category',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='tag_sequences',
        db_index=True,
        help_text="The asset category this sequence applies to. Null represents default sequences."
    )
    prefix = models.CharField(max_length=20, default='ASSET-', help_text="Prefix for generated asset tags (e.g. ASSET-)")
    next_value = models.PositiveIntegerField(default=1)
    zero_padding = models.PositiveSmallIntegerField(default=6)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Asset Tag Sequence")
        verbose_name_plural = _("Asset Tag Sequences")
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'prefix'],
                condition=models.Q(tenant__isnull=False),
                name='unique_tenant_prefix'
            ),
            models.UniqueConstraint(
                fields=['prefix'],
                condition=models.Q(tenant__isnull=True),
                name='unique_global_prefix'
            )
        ]

    def __str__(self):
        return f'{self.prefix} (next: {self.next_value:0{self.zero_padding}d})'

    def get_absolute_url(self):
        return reverse('assets:assettagsequence_detail', kwargs={'pk': self.pk})

    def next_tag(self):
        from django.db.models import F
        tag = f'{self.prefix}{self.next_value:0{self.zero_padding}d}'
        type(self).all_objects.filter(pk=self.pk).update(next_value=F('next_value') + 1)
        self.refresh_from_db(fields=['next_value'])
        return tag

    @property
    def next_tag_preview(self):
        return f'{self.prefix}{self.next_value:0{self.zero_padding}d}'

    @classmethod
    def get_next_tag_for_asset(cls, asset):
        """
        Resolves the next asset tag for the given asset based on a hierarchical fallback chain:
        1. Tenant-specific + Category-specific sequence
        2. Tenant-specific default sequence (no category)
        3. Global + Category-specific sequence
        4. Global default sequence (prefix='ASSET-', created if missing)
        """
        # 1. Tenant + Category specific
        if asset.tenant and asset.category:
            seq = cls.all_objects.filter(tenant=asset.tenant, category=asset.category, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 2. Tenant default (no category)
        if asset.tenant:
            seq = cls.all_objects.filter(tenant=asset.tenant, category__isnull=True, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 3. Global + Category specific
        if asset.category:
            seq = cls.all_objects.filter(tenant__isnull=True, category=asset.category, is_active=True).first()
            if seq:
                return seq.next_tag()

        # 4. Global default (no tenant, no category, prefix='ASSET-')
        seq, _ = cls.all_objects.get_or_create(
            tenant__isnull=True,
            category__isnull=True,
            prefix='ASSET-',
            defaults={'next_value': 1, 'zero_padding': 6, 'is_active': True}
        )
        return seq.next_tag()

    @classmethod
    def resolve_sequence_for_asset(cls, asset):
        """
        Resolves the matching sequence object for the asset based on the fallback chain.
        Does not increment or modify the sequence.
        """
        # 1. Tenant + Category specific
        if asset.tenant and asset.category:
            seq = cls.all_objects.filter(tenant=asset.tenant, category=asset.category, is_active=True).first()
            if seq:
                return seq

        # 2. Tenant default (no category)
        if asset.tenant:
            seq = cls.all_objects.filter(tenant=asset.tenant, category__isnull=True, is_active=True).first()
            if seq:
                return seq

        # 3. Global + Category specific
        if asset.category:
            seq = cls.all_objects.filter(tenant__isnull=True, category=asset.category, is_active=True).first()
            if seq:
                return seq

        # 4. Global default (no tenant, no category, prefix='ASSET-')
        seq, _ = cls.all_objects.get_or_create(
            tenant__isnull=True,
            category__isnull=True,
            prefix='ASSET-',
            defaults={'next_value': 1, 'zero_padding': 6, 'is_active': True}
        )
        return seq



class AssetAssignment(JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name='assignments', db_index=True
    )
    assigned_user = models.ForeignKey(
        'organization.AssetHolder', on_delete=models.CASCADE, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_location = models.ForeignKey(
        'organization.Location', on_delete=models.CASCADE, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_asset = models.ForeignKey(
        'Asset', on_delete=models.CASCADE, null=True, blank=True, related_name='child_assignments'
    )
    pre_checkout_status = models.ForeignKey(
        'StatusLabel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assignment_pre_checkouts',
        help_text="Preserved status label to revert to upon checkin."
    )

    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checkouts'
    )
    checked_out_at = models.DateTimeField(default=timezone.now)
    expected_checkin_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='checkins'
    )
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='asset_assignments', blank=True)

    class Meta:
        ordering = ['-checked_out_at']
        constraints = [
            models.UniqueConstraint(
                fields=['asset'],
                condition=models.Q(is_active=True),
                name='unique_active_assignment_per_asset'
            ),
            models.CheckConstraint(
                check=(
                    models.Q(assigned_user__isnull=False, assigned_location__isnull=True, assigned_asset__isnull=True) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=False, assigned_asset__isnull=True) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False)
                ),
                name='exactly_one_assignment_target'
            )
        ]
        verbose_name = _("Asset Assignment")
        verbose_name_plural = _("Asset Assignments")

    @property
    def assigned_target(self):
        return self.assigned_user or self.assigned_location or self.assigned_asset

    @property
    def assigned_to(self):
        return self.assigned_target

    @property
    def assigned_to_type(self):
        if self.assigned_user: return 'assetholder'
        if self.assigned_location: return 'location'
        if self.assigned_asset: return 'asset'
        return None

    def __str__(self):
        return f"{self.asset} → {self.assigned_target} ({'active' if self.is_active else 'inactive'})"

    def get_absolute_url(self):
        return self.asset.get_absolute_url()


# --- Audit & Campaign Models ---
from core.mixins import SoftDeleteMixin

class AuditSession(StandardModel, SoftDeleteMixin):
    name = models.CharField(max_length=200)
    location = models.ForeignKey(
        'organization.Location', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Target location expected to be audited. If omitted, applies globally."
    )
    status = models.CharField(
        max_length=20, 
        choices=[
            ('planned', 'Planned'),
            ('active', 'Active'),
            ('completed', 'Completed'),
        ], 
        default='planned'
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='audit_sessions')

    class Meta:
        ordering = ['-started_at']
        verbose_name = _("Audit Session")
        verbose_name_plural = _("Audit Sessions")

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('assets:auditsession_detail', kwargs={'pk': self.pk})

    @property
    def expected_assets_queryset(self):
        """Returns QuerySet of Assets expected to be at this location."""
        qs = Asset.objects.exclude(status__type=StatusLabel.TYPE_ARCHIVED)
        if not self.location:
            return qs.filter(status__type__in=[
                StatusLabel.TYPE_DEPLOYABLE,
                StatusLabel.TYPE_PENDING,
                StatusLabel.TYPE_DEPLOYED
            ])
        return qs.filter(location=self.location)


class AssetAudit(models.Model):
    session = models.ForeignKey(
        AuditSession, 
        on_delete=models.CASCADE, 
        related_name='audits', 
        null=True, 
        blank=True
    )
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='audits')
    auditor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='audits_performed')
    timestamp = models.DateTimeField(auto_now_add=True)
    location = models.ForeignKey(
        'organization.Location', 
        on_delete=models.PROTECT,
        help_text="The observed physical location of the asset during audit."
    )
    status = models.ForeignKey(
        StatusLabel, 
        on_delete=models.PROTECT,
        help_text="The observed physical status of the asset during audit."
    )
    notes = models.TextField(blank=True)
    verification_method = models.CharField(
        max_length=30,
        choices=[
            ('barcode', 'Barcode Scan'),
            ('rfid', 'RFID Reader'),
            ('manual', 'Manual Input'),
            ('auto', 'Agent API Handshake')
        ],
        default='manual'
    )

    class Meta:
        ordering = ['-timestamp']
        constraints = [
            models.UniqueConstraint(fields=['session', 'asset'], name='unique_session_asset')
        ]
        verbose_name = _("Asset Audit")
        verbose_name_plural = _("Asset Audits")


