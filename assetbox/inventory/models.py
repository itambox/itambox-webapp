from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q, CheckConstraint
from django.core.exceptions import ValidationError

from core.models import BaseModel, ChangeLoggingMixin, AssetBoxModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin
from core.managers import SoftDeleteManager, AllObjectsManager


class Accessory(AutoSlugMixin, SoftDeleteMixin, AssetBoxModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    
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
    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='accessories')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER, verbose_name="Accessory Type")
    notification_category = models.ForeignKey(
        'assets.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acc_categories',
        verbose_name="Notification Category",
        db_index=True
    )
    supplier = models.ForeignKey(
        'assets.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supplier_accessories',
        verbose_name="Supplier",
        db_index=True
    )
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or manufacturer part number")
    qty = models.PositiveIntegerField(default=0, verbose_name="Total Stock")
    min_qty = models.PositiveIntegerField(default=0, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
    allow_overallocate = models.BooleanField(default=False, verbose_name="Allow Over-allocation", help_text="Allow checkout count to exceed stock capacity")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='accessories', blank=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='accessories', db_index=True)

    slug_source = ('manufacturer__name', 'name')

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Accessory"
        verbose_name_plural = "Accessories"
        db_table = 'assets_accessory'
        app_label = 'assets'

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
        db_table = 'assets_accessoryassignment'
        app_label = 'assets'
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


class Consumable(AutoSlugMixin, SoftDeleteMixin, AssetBoxModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()
    
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
    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='consumables')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or manufacturer part number")
    qty = models.PositiveIntegerField(default=0, verbose_name="Total Quantity")
    min_qty = models.PositiveIntegerField(default=0, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
    allow_overallocate = models.BooleanField(default=False, verbose_name="Allow Over-allocation", help_text="Allow consumption count to exceed stock capacity")
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='consumables', blank=True)
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='consumables', db_index=True)

    slug_source = ('manufacturer__name', 'name')

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Consumable"
        verbose_name_plural = "Consumables"
        db_table = 'assets_consumable'
        app_label = 'assets'

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
        db_table = 'assets_consumableassignment'
        app_label = 'assets'
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


class Kit(JournalingMixin, TaggableMixin, ChangeLoggingMixin, BaseModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Kit Name")
    description = models.TextField(blank=True, verbose_name="Description")
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='kits', db_index=True)
    tags = models.ManyToManyField('extras.Tag', related_name='kits', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Kit"
        verbose_name_plural = "Kits"
        db_table = 'assets_kit'
        app_label = 'assets'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('assets:kit_detail', kwargs={'pk': self.pk})


class KitItem(ChangeLoggingMixin, BaseModel):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name='items', verbose_name="Kit", db_index=True)
    asset_type = models.ForeignKey('assets.AssetType', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Asset Type / Model")
    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Accessory Catalog Item")
    license = models.ForeignKey('licenses.License', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Software License")
    qty = models.PositiveIntegerField(default=1, verbose_name="Quantity", help_text="Quantity to checkout (only applies to Accessories)")

    class Meta:
        verbose_name = "Kit Item"
        verbose_name_plural = "Kit Items"
        db_table = 'assets_kititem'
        app_label = 'assets'
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
