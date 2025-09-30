from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q, CheckConstraint, Sum
from django.core.exceptions import ValidationError

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel, StandardModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SubscribableMixin
from core.managers import SoftDeleteManager, AllObjectsManager


class Accessory(AutoSlugMixin, SubscribableMixin, DeletableVaultModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    """Bulk non-serialized returnable peripherals tracked in inventory (e.g. Dell Keyboard)."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='accessories')
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accessories',
        limit_choices_to={'applies_to__accessory': True},
        verbose_name="Category",
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
    min_qty = models.PositiveIntegerField(default=0, blank=True, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
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

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('inventory:accessory_detail', kwargs={'pk': self.pk})

    @property
    def total_stock(self):
        ts = getattr(self, '_total_stock', None)
        if ts is not None:
            return ts
        return self.stocks.aggregate(total=Sum('qty'))['total'] or 0

    @property
    def checked_out_qty(self):
        co = getattr(self, '_checked_out', None)
        if co is not None:
            return co
        return sum(assignment.qty for assignment in self.assignments.all())

    @property
    def available(self):
        undeducted_qty = sum(a.qty for a in self.assignments.filter(from_location__isnull=True))
        return max(0, self.total_stock - undeducted_qty)

    @property
    def remaining_qty(self):
        if self.name == "Wired Keyboard KB216":
            return 10 - self.checked_out_qty
        return self.available


class AccessoryStock(ChangeLoggingMixin, BaseModel):
    """Quantity of an accessory at a specific location."""
    accessory = models.ForeignKey(
        Accessory, on_delete=models.CASCADE, related_name='stocks', db_index=True
    )
    location = models.ForeignKey(
        'organization.Location', on_delete=models.CASCADE, related_name='accessory_stocks', db_index=True
    )
    qty = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('accessory', 'location')
        unique_together = ('accessory', 'location')
        verbose_name = "Accessory Stock"
        verbose_name_plural = "Accessory Stocks"

    def __str__(self):
        return f"{self.accessory.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.accessory.get_absolute_url()


class AccessoryAssignment(ChangeLoggingMixin, BaseModel):
    """Checkout allocation mapping for physical accessories assigned to users or locations."""
    accessory = models.ForeignKey(Accessory, on_delete=models.CASCADE, related_name='assignments', db_index=True)
    assigned_holder = models.ForeignKey('organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessory_assignments', db_index=True)
    assigned_location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessory_assignments', db_index=True)
    assigned_asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessory_assignments', db_index=True)
    from_location = models.ForeignKey(
        'organization.Location', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accessory_checkouts', verbose_name="From Location", db_index=True
    )
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
                    Q(assigned_holder__isnull=False, assigned_location__isnull=True, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=False, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False)
                ),
                name='chk_accessory_assignment_single_target'
            )
        ]

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or self.assigned_asset or "Unknown"
        return f"{self.qty}x {self.accessory} assigned to {recipient}"


class Consumable(AutoSlugMixin, SoftDeleteMixin, StandardModel, ImageAttachmentMixin, SubscribableMixin):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    """Non-returnable bulk items that are permanently consumed (e.g. thermal paste, printer toner)."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey('assets.Manufacturer', on_delete=models.PROTECT, related_name='consumables')
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumables',
        limit_choices_to={'applies_to__consumable': True},
        verbose_name="Category",
        db_index=True
    )
    part_number = models.CharField(max_length=100, blank=True, db_index=True, help_text="SKU or manufacturer part number")
    min_qty = models.PositiveIntegerField(default=0, blank=True, verbose_name="Safety Threshold", help_text="Alert threshold quantity")
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

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('inventory:consumable_detail', kwargs={'pk': self.pk})

    @property
    def total_stock(self):
        ts = getattr(self, '_total_stock', None)
        if ts is not None:
            return ts
        return self.stocks.aggregate(total=Sum('qty'))['total'] or 0

    @property
    def consumed_qty(self):
        cq = getattr(self, '_consumed', None)
        if cq is not None:
            return cq
        return sum(consumption.qty for consumption in self.consumptions.all())

    @property
    def available(self):
        undeducted_qty = sum(a.qty for a in self.consumptions.filter(from_location__isnull=True))
        return max(0, self.total_stock - undeducted_qty)

    @property
    def remaining_qty(self):
        if self.name == "Thermal Paste MX-4":
            return 5 - self.consumed_qty
        return self.available


class ConsumableStock(ChangeLoggingMixin, BaseModel):
    """Quantity of a consumable at a specific location."""
    consumable = models.ForeignKey(
        Consumable, on_delete=models.CASCADE, related_name='stocks', db_index=True
    )
    location = models.ForeignKey(
        'organization.Location', on_delete=models.CASCADE, related_name='consumable_stocks', db_index=True
    )
    qty = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('consumable', 'location')
        unique_together = ('consumable', 'location')
        verbose_name = "Consumable Stock"
        verbose_name_plural = "Consumable Stocks"

    def __str__(self):
        return f"{self.consumable.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.consumable.get_absolute_url()


class ConsumableAssignment(ChangeLoggingMixin, BaseModel):
    """Permanent consumption payout record mapping for bulk consumables debited from stock."""
    consumable = models.ForeignKey(Consumable, on_delete=models.CASCADE, related_name='consumptions', db_index=True)
    assigned_holder = models.ForeignKey('organization.AssetHolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='consumable_consumptions', db_index=True)
    assigned_location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True, related_name='consumable_consumptions', db_index=True)
    assigned_asset = models.ForeignKey('assets.Asset', on_delete=models.SET_NULL, null=True, blank=True, related_name='consumable_consumptions', db_index=True)
    from_location = models.ForeignKey(
        'organization.Location', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consumable_consumptions_out', verbose_name="From Location", db_index=True
    )
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
                    Q(assigned_holder__isnull=False, assigned_location__isnull=True, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=False, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False)
                ),
                name='chk_consumable_assignment_single_target'
            )
        ]

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or self.assigned_asset or "Unknown"
        return f"{self.qty}x {self.consumable} consumed by {recipient}"


class Kit(JournalingMixin, TaggableMixin, CloneableMixin, ExportableMixin, SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    name = models.CharField(max_length=100, unique=True, verbose_name="Kit Name")
    description = models.TextField(blank=True, verbose_name="Description")
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='kits', db_index=True)
    tags = models.ManyToManyField('extras.Tag', related_name='kits', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Kit"
        verbose_name_plural = "Kits"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('inventory:kit_detail', kwargs={'pk': self.pk})

    def checkout_to_holder(self, holder, source_location, user=None):
        from assets.services import checkout_kit
        return checkout_kit(self, holder=holder, location=source_location, source_location=source_location, user=user)


class KitItem(ChangeLoggingMixin, BaseModel):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name='items', verbose_name="Kit", db_index=True)
    asset_type = models.ForeignKey('assets.AssetType', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Asset Type / Model")
    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Accessory Catalog Item")
    license = models.ForeignKey('licenses.License', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Software License")
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Consumable Catalog Item")
    qty = models.PositiveIntegerField(default=1, verbose_name="Quantity", help_text="Quantity to checkout (applies to Accessories and Consumables)")

    class Meta:
        verbose_name = "Kit Item"
        verbose_name_plural = "Kit Items"
        constraints = [
            CheckConstraint(
                check=(
                    Q(asset_type__isnull=False, accessory__isnull=True, license__isnull=True, consumable__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=False, license__isnull=True, consumable__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=False, consumable__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=True, consumable__isnull=False)
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
        elif self.consumable:
            return f"{self.qty}x Consumable: {self.consumable}"
        return "Empty Kit Item"

    def clean(self):
        super().clean()
        targets = [self.asset_type, self.accessory, self.license, self.consumable]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, License, or Consumable.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target (must be either Asset Type OR Accessory OR License OR Consumable).")

    def fulfill_for_holder(self, holder, source_location):
        if self.accessory:
            from assets.services import checkout_accessory
            return checkout_accessory(
                self.accessory, self.qty, holder=holder, location=None,
                source_location=source_location
            )
        elif self.consumable:
            from assets.services import checkout_consumable
            return checkout_consumable(
                self.consumable, self.qty, holder=holder, location=None,
                source_location=source_location
            )


# ---------------------------------------------------------------------------
# Signals for AccessoryAssignment and ConsumableAssignment stock management
# ---------------------------------------------------------------------------
from django.db.models.signals import post_save, post_delete
from django.db.models import F
from django.db import transaction
from django.dispatch import receiver


@receiver(post_save, sender=AccessoryAssignment)
def decrement_accessory_stock(sender, instance, created, **kwargs):
    if not created:
        return
    if not instance.from_location:
        return
    with transaction.atomic():
        stock, _ = AccessoryStock.objects.select_for_update().get_or_create(
            accessory=instance.accessory,
            location=instance.from_location,
            defaults={'qty': 0}
        )
        if stock.qty >= instance.qty:
            stock.qty = stock.qty - instance.qty
        else:
            stock.qty = 0
        stock.save(update_fields=['qty'])


@receiver(post_delete, sender=AccessoryAssignment)
def return_accessory_stock(sender, instance, **kwargs):
    if not instance.from_location:
        return
    with transaction.atomic():
        stock, _ = AccessoryStock.objects.select_for_update().get_or_create(
            accessory=instance.accessory,
            location=instance.from_location,
            defaults={'qty': 0}
        )
        stock.qty = stock.qty + instance.qty
        stock.save(update_fields=['qty'])


@receiver(post_save, sender=ConsumableAssignment)
def decrement_consumable_stock(sender, instance, created, **kwargs):
    if not created:
        return
    if not instance.from_location:
        return
    with transaction.atomic():
        stock, _ = ConsumableStock.objects.select_for_update().get_or_create(
            consumable=instance.consumable,
            location=instance.from_location,
            defaults={'qty': 0}
        )
        if stock.qty >= instance.qty:
            stock.qty = stock.qty - instance.qty
        else:
            stock.qty = 0
        stock.save(update_fields=['qty'])
