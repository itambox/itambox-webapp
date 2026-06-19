from django.db import models
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q, CheckConstraint, Sum
from django.core.exceptions import ValidationError, FieldError

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel, StandardModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SubscribableMixin
from core.managers import SoftDeleteManager, AllObjectsManager, TenantScopingManager, TenantScopingSoftDeleteManager, TenantScopingAllObjectsManager, SoftDeleteQuerySet, TenantScopingQuerySet, TenantScopingSoftDeleteQuerySet
from .abstract_models import AbstractInventoryItem, AbstractStock, AbstractAssignment


class AccessoryQuerySet(TenantScopingSoftDeleteQuerySet):
    def with_counts(self):
        from django.db.models import Sum, OuterRef, Subquery, IntegerField
        from django.db.models.functions import Coalesce

        # One independent correlated Subquery per multi-valued reverse relation.
        # Annotating two Sum() over different relations in a single .annotate()
        # produces a |stocks|x|assignments| cartesian JOIN that inflates BOTH
        # sums; Subqueries keep each aggregate independent and un-inflated.
        total_stock_subquery = AccessoryStock.objects.filter(
            accessory=OuterRef('pk')
        ).order_by().values('accessory').annotate(
            total=Sum('qty')
        ).values('total')

        checked_out_subquery = AccessoryAssignment.objects.filter(
            accessory=OuterRef('pk'),
            deleted_at__isnull=True
        ).order_by().values('accessory').annotate(
            total=Sum('qty')
        ).values('total')

        return self.annotate(
            _total_stock=Coalesce(Subquery(total_stock_subquery, output_field=IntegerField()), 0),
            _checked_out=Coalesce(Subquery(checked_out_subquery, output_field=IntegerField()), 0)
        )


class TenantScopingAccessoryManager(models.Manager.from_queryset(AccessoryQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset().filter_by_tenant()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class AllObjectsAccessoryManager(models.Manager.from_queryset(AccessoryQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter_by_tenant()


class ConsumableQuerySet(TenantScopingSoftDeleteQuerySet):
    def with_counts(self):
        from django.db.models import Sum, OuterRef, Subquery, IntegerField
        from django.db.models.functions import Coalesce

        # See AccessoryQuerySet.with_counts: independent Subqueries avoid the
        # stocks x consumptions cartesian-product double-count.
        total_stock_subquery = ConsumableStock.objects.filter(
            consumable=OuterRef('pk')
        ).order_by().values('consumable').annotate(
            total=Sum('qty')
        ).values('total')

        consumed_subquery = ConsumableAssignment.objects.filter(
            consumable=OuterRef('pk'),
            deleted_at__isnull=True
        ).order_by().values('consumable').annotate(
            total=Sum('qty')
        ).values('total')

        return self.annotate(
            _total_stock=Coalesce(Subquery(total_stock_subquery, output_field=IntegerField()), 0),
            _consumed=Coalesce(Subquery(consumed_subquery, output_field=IntegerField()), 0)
        )


class TenantScopingConsumableManager(models.Manager.from_queryset(ConsumableQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset().filter_by_tenant()
        try:
            return qs.filter(deleted_at__isnull=True)
        except FieldError:
            return qs


class AllObjectsConsumableManager(models.Manager.from_queryset(ConsumableQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter_by_tenant()


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

    specs = models.JSONField(default=dict, blank=True, verbose_name=_("Specs"))
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='components',
        verbose_name=_("Tenant"),
        db_index=True
    )
    tags = models.ManyToManyField(
        'extras.Tag',
        related_name='components',
        verbose_name=_("Tags"),
        blank=True
    )

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Component (Catalog)")
        verbose_name_plural = _("Components (Catalog)")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                condition=models.Q(deleted_at__isnull=True),
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
    objects = TenantScopingAccessoryManager()
    all_objects = AllObjectsAccessoryManager()

    manufacturer = models.ForeignKey(
        'assets.Manufacturer',
        on_delete=models.PROTECT,
        related_name='accessories',
        verbose_name=_("Manufacturer")
    )
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='accessories',
        verbose_name=_("Category"),
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
        verbose_name=_("Tenant"),
        db_index=True
    )

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Accessory")
        verbose_name_plural = _("Accessories")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                condition=models.Q(deleted_at__isnull=True),
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


class Consumable(AbstractInventoryItem):
    objects = TenantScopingConsumableManager()
    all_objects = AllObjectsConsumableManager()

    class Meta(AbstractInventoryItem.Meta):
        verbose_name = _("Consumable")
        verbose_name_plural = _("Consumables")
        constraints = [
            models.UniqueConstraint(
                fields=['manufacturer', 'name'],
                condition=models.Q(deleted_at__isnull=True),
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


class ComponentStock(AbstractStock):
    tenant_lookup = 'component__tenant'
    objects = TenantScopingManager()

    @property
    def tenant(self):
        return self.component.tenant if self.component_id else None

    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name='stocks', verbose_name=_("Component"), db_index=True
    )
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.PROTECT,
        related_name='component_stocks',
        verbose_name=_("Location"),
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
    tenant_lookup = 'accessory__tenant'
    objects = TenantScopingManager()

    @property
    def tenant(self):
        return self.accessory.tenant if self.accessory_id else None

    accessory = models.ForeignKey(
        Accessory, on_delete=models.PROTECT, related_name='stocks', verbose_name=_("Accessory"), db_index=True
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
    tenant_lookup = 'consumable__tenant'
    objects = TenantScopingManager()

    @property
    def tenant(self):
        return self.consumable.tenant if self.consumable_id else None

    consumable = models.ForeignKey(
        Consumable, on_delete=models.PROTECT, related_name='stocks', verbose_name=_("Consumable"), db_index=True
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
    tenant_lookup = 'component__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.component.tenant if self.component_id else None

    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name='allocations', verbose_name=_("Component"), db_index=True
    )
    assigned_asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='component_allocations',
        verbose_name=_("Assigned Asset"),
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
        verbose_name=_("Tags"),
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



class AccessoryAssignment(AbstractAssignment):
    tenant_lookup = 'accessory__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.accessory.tenant if self.accessory_id else None

    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, related_name='assignments', verbose_name=_("Accessory"), db_index=True)

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
    tenant_lookup = 'consumable__tenant'
    objects = TenantScopingSoftDeleteManager()
    all_objects = TenantScopingAllObjectsManager()

    @property
    def tenant(self):
        return self.consumable.tenant if self.consumable_id else None

    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='consumptions', verbose_name=_("Consumable"), db_index=True)

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
    all_objects = TenantScopingAllObjectsManager()
    allow_global_tenant = True

    name = models.CharField(max_length=100, verbose_name=_("Kit Name"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='kits', verbose_name=_("Tenant"), db_index=True)
    tags = models.ManyToManyField('extras.Tag', related_name='kits', verbose_name=_("Tags"), blank=True)

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
    tenant_lookup = 'kit__tenant'
    # Global Kits (tenant=None) are cross-tenant templates, so their items stay
    # READABLE by default (the manager's default behaviour for tenant_lookup
    # models). Cross-tenant WRITE is blocked by StrictTenantPermission /
    # perform_create tenant-defaulting.
    objects = TenantScopingManager()

    @property
    def tenant(self):
        return self.kit.tenant if self.kit_id else None

    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name='items', verbose_name=_("Kit"), db_index=True)
    asset_type = models.ForeignKey('assets.AssetType', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name=_("Asset Type / Model"))
    accessory = models.ForeignKey(Accessory, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name=_("Accessory Catalog Item"))
    license = models.ForeignKey('licenses.License', on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name=_("Software License"))
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name=_("Consumable Catalog Item"))
    component = models.ForeignKey(Component, on_delete=models.PROTECT, null=True, blank=True, related_name='kit_items', verbose_name=_("Component Catalog Item"))
    qty = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"), help_text=_("Quantity to checkout (applies to Accessories, Consumables, and Components)"))

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
            raise ValidationError(_("A kit item must select either an Asset Type, Accessory, License, Consumable, or Component."))
        if len(filled) > 1:
            raise ValidationError(_("A kit item cannot select more than one target."))
