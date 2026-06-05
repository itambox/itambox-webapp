from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.core.exceptions import FieldError
from core.models import BaseModel, ChangeLoggingMixin, StandardModel
from core.mixins import TaggableMixin, AutoSlugMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SoftDeleteMixin
from core.managers import SoftDeleteQuerySet, SoftDeleteManager, AllObjectsManager, TenantScopingQuerySet


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

        # Subquery to sum active qty_allocated in ComponentAllocation for this component
        allocated_stock_subquery = ComponentAllocation.objects.filter(
            component=OuterRef('pk'),
            deleted_at__isnull=True
        ).order_by().values('component').annotate(
            total=Sum('qty_allocated')
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


class Component(AutoSlugMixin, SoftDeleteMixin, StandardModel, ImageAttachmentMixin):
    objects = TenantScopingComponentManager()
    all_objects = AllObjectsComponentManager()
    allow_global_tenant = True

    """Catalog entry for a hardware component (e.g. 'Crucial 16GB DDR4')."""
    slug_source = ('manufacturer__name', 'name')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    manufacturer = models.ForeignKey(
        'assets.Manufacturer', on_delete=models.PROTECT, related_name='components'
    )
    category = models.ForeignKey(
        'assets.Category', on_delete=models.PROTECT, related_name='components',
        limit_choices_to={'applies_to__component': True}
    )
    part_number = models.CharField(max_length=100, blank=True, db_index=True)
    specs = models.JSONField(default=dict, blank=True)
    min_stock_level = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='components',
        db_index=True
    )
    tags = models.ManyToManyField('extras.Tag', related_name='new_components', blank=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = _("Component (Catalog)")
        verbose_name_plural = _("Components (Catalog)")

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"

    def get_absolute_url(self):
        return reverse('components:component_detail', kwargs={'pk': self.pk})

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
            total=models.Sum('qty_allocated')
        )['total'] or 0

    @property
    def available_stock(self):
        return self.total_stock - self.total_allocated


class ComponentStock(ChangeLoggingMixin, BaseModel):
    """Quantity of a component at a specific location."""
    component = models.ForeignKey(
        Component, on_delete=models.CASCADE, related_name='stocks', db_index=True
    )
    location = models.ForeignKey(
        'organization.Location', on_delete=models.CASCADE, related_name='component_stocks', db_index=True
    )
    qty = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('component', 'location')
        unique_together = ('component', 'location')
        verbose_name = _("Component Stock")
        verbose_name_plural = _("Component Stocks")

    def __str__(self):
        return f"{self.component.name} @ {self.location.name}: {self.qty}"

    def get_absolute_url(self):
        return self.component.get_absolute_url()


class ComponentAllocation(JournalingMixin, TaggableMixin, SoftDeleteMixin, BaseModel):
    """Quantity of a component allocated to a specific asset."""
    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    component = models.ForeignKey(
        Component, on_delete=models.CASCADE, related_name='allocations', db_index=True
    )
    asset = models.ForeignKey(
        'assets.Asset', on_delete=models.CASCADE, related_name='component_allocations', db_index=True
    )
    from_location = models.ForeignKey(
        'organization.Location', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='component_allocations', verbose_name="From Location", db_index=True
    )
    qty_allocated = models.PositiveIntegerField(default=1)
    allocated_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='component_allocations', blank=True)

    class Meta:
        ordering = ('-allocated_at',)
        verbose_name = _("Component Allocation")
        verbose_name_plural = _("Component Allocations")

    def __str__(self):
        return f"{self.component.name} → {self.asset.name} (qty: {self.qty_allocated})"

    def get_absolute_url(self):
        return self.asset.get_absolute_url()

    def _get_target_location(self, from_location):
        target_location = from_location
        if not target_location and self.asset:
            target_location = self.asset.location
        return target_location

    def _deduct_stock(self, component, from_location, qty):
        loc = self._get_target_location(from_location)
        if not loc:
            return
        stock, _ = ComponentStock.objects.select_for_update().get_or_create(
            component=component,
            location=loc,
            defaults={'qty': 0}
        )
        if stock.qty >= qty:
            stock.qty -= qty
        else:
            stock.qty = 0
        stock.save(update_fields=['qty'])

    def _return_stock(self, component, from_location, qty):
        loc = self._get_target_location(from_location)
        if not loc:
            return
        stock, _ = ComponentStock.objects.select_for_update().get_or_create(
            component=component,
            location=loc,
            defaults={'qty': 0}
        )
        stock.qty += qty
        stock.save(update_fields=['qty'])

    def save(self, *args, **kwargs):
        from django.db import transaction
        from django.core.exceptions import ValidationError
        from .models import ComponentStock

        with transaction.atomic():
            is_new = self.pk is None
            if is_new:
                if not self.from_location and self.asset:
                    self.from_location = self.asset.location
                if self.deleted_at is None:
                    self._deduct_stock(self.component, self.from_location, self.qty_allocated)
            else:
                old = ComponentAllocation.all_objects.get(pk=self.pk)
                
                was_active = old.deleted_at is None
                is_active = self.deleted_at is None
                
                if was_active and not is_active:
                    # Soft-deleted: return stock
                    self._return_stock(old.component, old.from_location, old.qty_allocated)
                elif not was_active and is_active:
                    # Restored: deduct stock
                    self._deduct_stock(self.component, self.from_location, self.qty_allocated)
                elif is_active:
                    # Normal update: revert old, apply new!
                    self._return_stock(old.component, old.from_location, old.qty_allocated)
                    self._deduct_stock(self.component, self.from_location, self.qty_allocated)
            
            super().save(*args, **kwargs)

    def delete(self, *args, force_hard_delete=False, **kwargs):
        from django.db import transaction
        with transaction.atomic():
            if force_hard_delete and self.deleted_at is None:
                self._return_stock(self.component, self.from_location, self.qty_allocated)
            super().delete(*args, force_hard_delete=force_hard_delete, **kwargs)
