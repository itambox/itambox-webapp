from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from software.models import Software
from core.models import BaseModel, ChangeLoggingMixin, StandardModel, DeletableVaultModel
from core.mixins import CustomFieldDataMixin, JournalingMixin, TaggableMixin, AutoSlugMixin, BookmarkableMixin, SubscribableMixin, SoftDeleteMixin, CloneableMixin, ExportableMixin, ImageAttachmentMixin, FileAttachmentMixin
from extras.models import CustomFieldset
from django.contrib.contenttypes.fields import GenericRelation, GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from assets.choices import StatusTypeChoices, RequestStatusChoices

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
    # Back-compat aliases — canonical definitions live in assets.choices.
    TYPE_DEPLOYABLE = StatusTypeChoices.DEPLOYABLE
    TYPE_DEPLOYED = StatusTypeChoices.DEPLOYED
    TYPE_PENDING = StatusTypeChoices.PENDING
    TYPE_UNDEPLOYABLE = StatusTypeChoices.UNDEPLOYABLE
    TYPE_ARCHIVED = StatusTypeChoices.ARCHIVED
    TYPE_CHOICES = StatusTypeChoices.choices

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    type = models.CharField(max_length=50, choices=StatusTypeChoices.choices, default=StatusTypeChoices.DEPLOYABLE, db_index=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    tags = models.ManyToManyField('extras.Tag', related_name='status_labels_tagged', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Status Label")
        verbose_name_plural = _("Status Labels")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_statuslabel_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_statuslabel_slug_active'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    def get_absolute_url(self):
        return reverse('assets:statuslabel_detail', kwargs={'pk': self.pk})



class AssetRole(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    """Categorizes assets based on their functional role (e.g., Laptop, Monitor, Server)."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    allows_components = models.BooleanField(
        default=False,
        help_text=_("Assets with this role can have components allocated (servers, workstations, …)"),
    )
    tags = models.ManyToManyField(
        to='extras.Tag',
        related_name='asset_roles',
        blank=True
    )

    class Meta:
        verbose_name = _("Asset Role")
        verbose_name_plural = _("Asset Roles")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_assetrole_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_assetrole_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        # Use standardized URL name
        return reverse('assets:assetrole_detail', args=[self.pk])

class Manufacturer(StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)

    contacts = GenericRelation('organization.ContactAssignment')
    tags = models.ManyToManyField('extras.Tag', related_name='manufacturers', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Manufacturer")
        verbose_name_plural = _("Manufacturers")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_manufacturer_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_manufacturer_slug_active'),
        ]

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

    class Method(models.TextChoices):
        STRAIGHT_LINE = 'straight_line', _('Straight-line')
        NONE = 'none', _('None (no depreciation)')

    class Convention(models.TextChoices):
        EXCLUDE_PURCHASE_MONTH = 'exclude_purchase_month', _('Exclude purchase month (month diff)')
        INCLUDE_PURCHASE_MONTH = 'include_purchase_month', _('Include purchase month (pro rata temporis)')

    name = models.CharField(max_length=100, verbose_name=_("Depreciation Name"))
    months = models.PositiveIntegerField(
        verbose_name=_("Lifespan (Months)"),
        help_text=_("Useful lifespan in months for straight-line calculations"),
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.STRAIGHT_LINE,
        verbose_name=_("Method"),
    )
    convention = models.CharField(
        max_length=30,
        choices=Convention.choices,
        default=Convention.INCLUDE_PURCHASE_MONTH,
        verbose_name=_("Convention"),
        help_text=_("Determines whether the acquisition month counts as a full depreciation month."),
    )
    immediate_expense_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Immediate expense threshold (GWG)"),
        help_text=_("Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG)."),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))

    class Meta:
        ordering = ['name']
        verbose_name = _("Depreciation")
        verbose_name_plural = _("Depreciations")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_depreciation_name_active'),
        ]

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
    slug = models.SlugField(max_length=255)
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
    # custom_field_data JSONField comes from CustomFieldDataMixin
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
            models.UniqueConstraint(
                fields=['manufacturer', 'model'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_manufacturer_model_active',
            ),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_assettype_slug_active'),
        ]
        verbose_name = _("Asset Type")
        verbose_name_plural = _("Asset Types")


    def __str__(self):
        return f"{self.manufacturer.name} {self.model}"

    def get_absolute_url(self):
        return reverse('assets:assettype_detail', kwargs={'pk': self.pk})



class Asset(CustomFieldDataMixin, BookmarkableMixin, SubscribableMixin, DeletableVaultModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    # NOTE: asset status is a FK to StatusLabel; the lifecycle vocabulary is
    # StatusLabel.type (assets.choices.StatusTypeChoices), not a local choice set.

    name = models.CharField(max_length=255)
    asset_tag = models.CharField(max_length=50, blank=True)
    serial_number = models.CharField(max_length=100, blank=True, db_index=True)
    asset_type = models.ForeignKey(AssetType, on_delete=models.PROTECT, related_name='assets', null=True, blank=True, db_index=True)
    asset_role = models.ForeignKey(AssetRole, on_delete=models.SET_NULL, blank=True, null=True, related_name='assets', db_index=True)
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
    notes = models.TextField(blank=True)
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
    # custom_field_data JSONField comes from CustomFieldDataMixin
    requestable = models.BooleanField(null=True, blank=True, default=None, db_index=True, help_text="Allow users to request this asset")
    depreciation_override = models.ForeignKey(
        'Depreciation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_overrides',
        verbose_name=_("Depreciation override"),
        help_text=_("Override depreciation policy — leave empty to use the tenant default or asset-type schedule."),
    )
    in_service_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("In-service date"),
        help_text=_("Depreciation starts here; falls back to purchase date."),
    )
    disposed_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name=_("Disposed at"))
    disposal_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        verbose_name=_("Sign-off value"),
    )

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

    @property
    def audit_due_date(self):
        """Date by which the next physical audit is due, or None if no cadence is set.

        Never-audited assets with a cadence are overdue immediately (returns created_at).
        """
        category = self.category
        if not category or not category.audit_interval_months:
            return None
        from datetime import timedelta
        interval_days = category.audit_interval_months * 30
        base = self.last_audited or self.created_at
        return base + timedelta(days=interval_days)

    @property
    def audit_overdue(self) -> bool:
        """True when a cadence is set and the due date has passed."""
        from django.utils import timezone
        due = self.audit_due_date
        return due is not None and timezone.now() > due

    def get_status_display(self):
        return self.status.name if self.status else "—"

    @property
    def eol_date(self):
        if self.purchase_date and self.asset_type and self.asset_type.eol_months:
            from dateutil.relativedelta import relativedelta
            # relativedelta clamps month-end overflow (Jan 31 + 1 month = Feb 28/29).
            return self.purchase_date + relativedelta(months=self.asset_type.eol_months)
        return None

    @property
    def time_to_eol(self):
        eol = self.eol_date
        if eol:
            import datetime
            from dateutil.relativedelta import relativedelta
            today = datetime.date.today()
            if today >= eol:
                return "Expired"

            delta = relativedelta(eol, today)
            parts = []
            if delta.years > 0:
                parts.append(f"{delta.years} year{'s' if delta.years != 1 else ''}")
            if delta.months > 0:
                parts.append(f"{delta.months} month{'s' if delta.months != 1 else ''}")
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
        """Estimated book value — delegates to the pure compute_book_value function."""
        from assets.depreciation import compute_book_value
        return compute_book_value(self)

    @property
    def is_modular(self):
        if self.component_allocations.filter(deleted_at__isnull=True).exists():
            return True
        return bool(self.asset_role and self.asset_role.allows_components)

    @property
    def active_assignment(self):
        prefetched = getattr(self, 'prefetched_active_assignments', None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return self.assignments.filter(is_active=True).first()

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
            # Integrity checks must see the row as stored, not through the current
            # request's tenant/soft-delete lens — otherwise a context mismatch
            # (background task, cross-tenant admin) silently skips the state machine.
            old_asset = Asset._base_manager.filter(pk=self.pk).first()
            if old_asset and old_asset.status_id and old_asset.status != self.status:
                AssetStateMachine.validate_transition(
                    old_asset.status.type,
                    self.status.type,
                    self.assignments.filter(is_active=True).exists()
                )

    def save(self, *args, **kwargs):
        if not self.asset_tag:
            self.asset_tag = AssetTagSequence.get_next_tag_for_asset(self)
        else:
            seq = AssetTagSequence.resolve_sequence_for_asset(self)
            if seq and self.asset_tag == seq.next_tag_preview:
                seq.next_tag()

        # Freeze/unfreeze sign-off value on archive transition.
        if self.pk:
            old = Asset._base_manager.filter(pk=self.pk).select_related('status').first()
            if old:
                old_type = old.status.type if old.status else None
                new_type = self.status.type if self.status else None
                if old_type != 'archived' and new_type == 'archived':
                    from assets.depreciation import compute_book_value
                    from decimal import Decimal
                    self.disposal_value = compute_book_value(self) or Decimal('0.00')
                    self.disposed_at = timezone.now()
                elif old_type == 'archived' and new_type != 'archived':
                    self.disposed_at = None
                    self.disposal_value = None

        super().save(*args, **kwargs)



class Supplier(CustomFieldDataMixin, AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    website = models.URLField(max_length=500, blank=True)
    contact_email = models.EmailField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='suppliers', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Supplier")
        verbose_name_plural = _("Suppliers")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_supplier_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_supplier_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:supplier_detail', kwargs={'pk': self.pk})




class Category(AutoSlugMixin, StandardModel, SoftDeleteMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    color = models.CharField(max_length=6, blank=True, help_text="RGB color in hexadecimal (e.g. 00ff00)")
    description = models.TextField(blank=True)
    applies_to = models.JSONField(default=dict, blank=True, help_text="Applies to: {'asset': True, 'accessory': True, 'component': True}")
    audit_interval_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How often assets in this category must be physically audited, in months. Leave blank for no required cadence."
    )
    tags = models.ManyToManyField('extras.Tag', related_name='categories', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True), name='unique_category_name_active'),
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_category_slug_active'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:category_detail', kwargs={'pk': self.pk})




class AssetRequest(JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()
    # Back-compat aliases — canonical definitions live in assets.choices.
    STATUS_PENDING = RequestStatusChoices.PENDING
    STATUS_APPROVED = RequestStatusChoices.APPROVED
    STATUS_PROCUREMENT = RequestStatusChoices.PROCUREMENT
    STATUS_DENIED = RequestStatusChoices.DENIED
    STATUS_FULFILLED = RequestStatusChoices.FULFILLED
    STATUS_CANCELLED = RequestStatusChoices.CANCELLED
    STATUS_CHOICES = RequestStatusChoices.choices

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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_requests'
    )
    assigned_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_requests'
    )
    assigned_asset = models.ForeignKey(
        'Asset',
        on_delete=models.SET_NULL,
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

    notes = models.TextField(blank=True)
    response_notes = models.TextField(blank=True)
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
                # _base_manager: state-machine checks must not depend on the active
                # tenant context (see Asset.clean).
                old_status = AssetRequest._base_manager.get(pk=self.pk).status
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
        from django.db import transaction
        from django.db.models import F
        # Lock the sequence row before reading: formatting the tag from an unlocked
        # read lets two concurrent saves claim the same value and collide on the
        # asset_tag unique constraint.
        with transaction.atomic():
            locked = type(self)._base_manager.select_for_update().get(pk=self.pk)
            tag = f'{locked.prefix}{locked.next_value:0{locked.zero_padding}d}'
            type(self)._base_manager.filter(pk=self.pk).update(next_value=F('next_value') + 1)
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



class AssetAssignment(SoftDeleteMixin, JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    # Tenant is derived from the parent asset; scope through it so assignments
    # cannot be listed or mutated across tenant boundaries.
    tenant_lookup = 'asset__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.asset.tenant if self.asset_id else None

    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name='assignments', db_index=True
    )
    assigned_user = models.ForeignKey(
        'organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_location = models.ForeignKey(
        'organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='asset_assignments'
    )
    assigned_asset = models.ForeignKey(
        'Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_assignments'
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
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False) |
                    models.Q(assigned_user__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=True)
                ),
                name='exactly_one_assignment_target'
            )
        ]
        verbose_name = _("Asset Assignment")
        verbose_name_plural = _("Asset Assignments")

    def clean(self):
        super().clean()
        targets = [self.assigned_user, self.assigned_location, self.assigned_asset]
        filled = [t for t in targets if t is not None]
        if self.is_active:
            if not filled:
                raise ValidationError(_("Either assigned_user, assigned_location, or assigned_asset must be provided for an active assignment."))
            if len(filled) > 1:
                raise ValidationError(_("You can only assign an asset to one target."))

            # Tenant boundary validation
            target = filled[0]
            if target and hasattr(target, 'tenant') and target.tenant != self.asset.tenant:
                raise ValidationError(_("Assignment target must belong to the same tenant as the asset."))

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


class MaintenanceStatusChoices(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    IN_PROGRESS = 'in_progress', 'In Progress'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


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

    asset = models.ForeignKey('Asset', on_delete=models.PROTECT, related_name='maintenances', db_index=True)
    title = models.CharField(max_length=200, default='Maintenance')
    description = models.TextField(blank=True)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Supplier/Vendor")
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
        return reverse('assets:assetmaintenance_detail', kwargs={'pk': self.pk})

    @property
    def downtime_days(self):
        if self.start_date and self.completion_date:
            return (self.completion_date - self.start_date).days
        return None


