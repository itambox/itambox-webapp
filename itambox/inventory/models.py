from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q, CheckConstraint, Sum
from django.core.exceptions import ValidationError, FieldError

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel, StandardModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SubscribableMixin
from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingSoftDeleteManager, SoftDeleteQuerySet, TenantScopingQuerySet
from .abstract_models import AbstractInventoryItem, AbstractStock, AbstractAssignment


class ComponentQuerySet(SoftDeleteQuerySet, TenantScopingQuerySet):
    def with_counts(self):
        from django.db.models import Sum, OuterRef, Subquery, IntegerField
        from django.db.models.functions import Coalesce

        # Subquery to sum the quantities in ComponentStock for this component
        total_stock_subquery = ComponentStock.objects.filter(
            component=OuterRef('pk')
        ).order_by().values('component').annotate(
            total=Sum('qty')
        ).values('total')

        # Subquery to sum active qty in ComponentAllocation for this component
        allocated_stock_subquery = ComponentAllocation.objects.filter(
            component=OuterRef('pk'),
            deleted_at__isnull=True
        ).order_by().values('component').annotate(
            total=Sum('qty')
        ).values('total')

        return self.annotate(
            _total_stock=Coalesce(Subquery(total_stock_subquery, output_field=IntegerField()), 0),
            _allocated_stock=Coalesce(Subquery(allocated_stock_subquery, output_field=IntegerField()), 0)
        )


class TenantScopingComponentManager(models.Manager.from_queryset(ComponentQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset().filter_by_tenant()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class AllObjectsComponentManager(models.Manager.from_queryset(ComponentQuerySet)):
    pass


class Component(AbstractInventoryItem):
    objects = TenantScopingComponentManager()
    all_objects = AllObjectsComponentManager()

    specs = models.JSONField(default=dict, blank=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='components',
        db_index=True
    )
    tags = models.ManyToManyField(
        'extras.Tag',
        related_name='new_components',
        blank=True
    )

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Component (Catalog)")
        verbose_name_plural = _("Components (Catalog)")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                name='unique_component_manufacturer_name'
            )
        ]

    def get_absolute_url(self):
        return reverse('inventory:component_detail', kwargs={'pk': self.pk})

    @property
    def total_stock(self):
        if hasattr(self, '_total_stock'):
            return self._total_stock
        return self.stocks.aggregate(total=models.Sum('qty'))['total'] or 0

    @property
    def total_allocated(self):
        if hasattr(self, '_allocated_stock'):
            return self._allocated_stock
        return self.allocations.filter(deleted_at__isnull=True).aggregate(
            total=models.Sum('qty')
        )['total'] or 0

    @property
    def available_stock(self):
        return self.total_stock - self.total_allocated

    @property
    def available(self):
        return self.available_stock


class Accessory(AbstractInventoryItem):
    objects = TenantScopingSoftDeleteManager()
    all_objects = AllObjectsManager()

    manufacturer = models.ForeignKey(
        'assets.Manufacturer',
        on_delete=models.PROTECT,
        related_name='accessories'
    )
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='accessories',
        db_index=True
    )
    supplier = models.ForeignKey(
        'assets.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supplier_accessories',
        verbose_name=_("Supplier"),
        db_index=True
    )
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='accessories',
        db_index=True
    )

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Accessory")
        verbose_name_plural = _("Accessories")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                name='inventory_accessory_unique_manufacturer_name'
            )
        ]

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
        return self.available


class Consumable(AbstractInventoryItem):
    objects = TenantScopingSoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Consumable")
        verbose_name_plural = _("Consumables")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                name='inventory_consumable_unique_manufacturer_name'
            )
        ]

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
        return self.available


class ComponentStock(AbstractStock):
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name='stocks', db_index=True
    )
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.PROTECT,
        related_name='component_stocks',
        db_index=True
    )

    class Meta(AbstractStock.Meta):
        verbose_name = _("Component Stock")
        verbose_name_plural = _("Component Stocks")
        constraints = [
            models.UniqueConstraint(
                fields=['component', 'location'],
                name='unique_component_location'
            )
        ]

    def __str__(self):
        return f"{self.component.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.component.get_absolute_url()


class AccessoryStock(AbstractStock):
    accessory = models.ForeignKey(
        Accessory, on_delete=models.PROTECT, related_name='stocks', db_index=True
    )

    class Meta(AbstractStock.Meta):
        verbose_name = _("Accessory Stock")
        verbose_name_plural = _("Accessory Stocks")
        constraints = [
            models.UniqueConstraint(
                fields=['accessory', 'location'],
                name='inventory_accessorystock_unique_accessory_location'
            )
        ]

    def __str__(self):
        return f"{self.accessory.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.accessory.get_absolute_url()


class ConsumableStock(AbstractStock):
    consumable = models.ForeignKey(
        Consumable, on_delete=models.PROTECT, related_name='stocks', db_index=True
    )

    class Meta(AbstractStock.Meta):
        verbose_name = _("Consumable Stock")
        verbose_name_plural = _("Consumable Stocks")
        constraints = [
            models.UniqueConstraint(
                fields=['consumable', 'location'],
                name='inventory_consumablestock_unique_consumable_location'
            )
        ]

    def __str__(self):
        return f"{self.consumable.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.consumable.get_absolute_url()


class ComponentAllocation(AbstractAssignment):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    def __init__(self, *args, **kwargs):
        if 'qty_allocated' in kwargs:
            kwargs['qty'] = kwargs.pop('qty_allocated')
        if 'asset' in kwargs:
            kwargs['assigned_asset'] = kwargs.pop('asset')
        super().__init__(*args, **kwargs)

    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name='allocations', db_index=True
    )
    assigned_asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='component_allocations',
        db_index=True
    )
    from_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='component_allocations',
        verbose_name=_("From Location"),
        db_index=True
    )
    tags = models.ManyToManyField(
        'extras.Tag',
        related_name='component_allocations',
        blank=True
    )

    class Meta(AbstractAssignment.Meta):
        verbose_name = _("Component Allocation")
        verbose_name_plural = _("Component Allocations")
        constraints = [
            CheckConstraint(
                check=(
                    Q(assigned_holder__isnull=False, assigned_location__isnull=True, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=False, assigned_asset__isnull=True) |
                    Q(assigned_holder__isnull=True, assigned_location__isnull=True, assigned_asset__isnull=False)
                ),
                name='chk_componentallocation_single_target'
            )
        ]

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or self.assigned_asset or "Unknown"
        return f"{self.qty}x {self.component} assigned to {recipient}"

    def get_absolute_url(self):
        if self.assigned_asset:
            return self.assigned_asset.get_absolute_url()
        return self.component.get_absolute_url()

    def save(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self, is_delete=True)
        super().delete(*args, **kwargs)

    @property
    def qty_allocated(self):
        return self.qty

    @qty_allocated.setter
    def qty_allocated(self, value):
        self.qty = value

    @property
    def asset(self):
        return self.assigned_asset

    @asset.setter
    def asset(self, value):
        self.assigned_asset = value



class AccessoryAssignment(AbstractAssignment):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, related_name='assignments', db_index=True)

    class Meta(AbstractAssignment.Meta):
        verbose_name = _("Accessory Assignment")
        verbose_name_plural = _("Accessory Assignments")
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

    def save(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self, is_delete=True)
        super().delete(*args, **kwargs)


class ConsumableAssignment(AbstractAssignment):
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='consumptions', db_index=True)

    class Meta(AbstractAssignment.Meta):
        verbose_name = _("Consumable Consumption")
        verbose_name_plural = _("Consumable Consumptions")
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

    def save(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .services import adjust_inventory_stock
        adjust_inventory_stock(self, is_delete=True)
        super().delete(*args, **kwargs)


class Kit(JournalingMixin, TaggableMixin, CloneableMixin, ExportableMixin, SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    objects = TenantScopingSoftDeleteManager()
    all_objects = AllObjectsManager()
    allow_global_tenant = True

    name = models.CharField(max_length=100, verbose_name="Kit Name")
    description = models.TextField(blank=True, verbose_name="Description")
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='kits', db_index=True)
    tags = models.ManyToManyField('extras.Tag', related_name='kits', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = _("Kit")
        verbose_name_plural = _("Kits")
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=Q(deleted_at__isnull=True), name='unique_kit_name_active'),
        ]

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
    component = models.ForeignKey(Component, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name="Component Catalog Item")
    qty = models.PositiveIntegerField(default=1, verbose_name="Quantity", help_text="Quantity to checkout (applies to Accessories, Consumables, and Components)")

    class Meta:
        verbose_name = _("Kit Item")
        verbose_name_plural = _("Kit Items")
        constraints = [
            CheckConstraint(
                check=(
                    Q(asset_type__isnull=False, accessory__isnull=True, license__isnull=True, consumable__isnull=True, component__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=False, license__isnull=True, consumable__isnull=True, component__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=False, consumable__isnull=True, component__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=True, consumable__isnull=False, component__isnull=True) |
                    Q(asset_type__isnull=True, accessory__isnull=True, license__isnull=True, consumable__isnull=True, component__isnull=False)
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
        elif self.component:
            return f"{self.qty}x Component: {self.component}"
        return "Empty Kit Item"

    def clean(self):
        super().clean()
        targets = [self.asset_type, self.accessory, self.license, self.consumable, self.component]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, License, Consumable, or Component.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target.")
