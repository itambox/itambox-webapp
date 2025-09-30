from django.db import models, transaction
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin, StandardModel
from core.mixins import TaggableMixin, AutoSlugMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SoftDeleteMixin
from core.managers import SoftDeleteQuerySet, SoftDeleteManager, AllObjectsManager


class ComponentQuerySet(SoftDeleteQuerySet):
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


class Component(AutoSlugMixin, SoftDeleteMixin, StandardModel, ImageAttachmentMixin):
    objects = SoftDeleteManager.from_queryset(ComponentQuerySet)()
    all_objects = AllObjectsManager.from_queryset(ComponentQuerySet)()

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
    tags = models.ManyToManyField('extras.Tag', related_name='new_components', blank=True)

    class Meta:
        ordering = ('manufacturer', 'name')
        unique_together = ('manufacturer', 'name')
        verbose_name = "Component (Catalog)"
        verbose_name_plural = "Components (Catalog)"

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
        verbose_name = "Component Stock"
        verbose_name_plural = "Component Stocks"

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
        verbose_name = "Component Allocation"
        verbose_name_plural = "Component Allocations"

    def __str__(self):
        return f"{self.component.name} → {self.asset.name} (qty: {self.qty_allocated})"

    def get_absolute_url(self):
        return self.asset.get_absolute_url()


from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver


def deduct_stock(instance):
    target_location = instance.from_location
    if not target_location and instance.asset:
        target_location = instance.asset.location
        
    if not target_location:
        return
        
    with transaction.atomic():
        stock, _ = ComponentStock.objects.select_for_update().get_or_create(
            component=instance.component,
            location=target_location,
            defaults={'qty': 0}
        )
        if stock.qty >= instance.qty_allocated:
            stock.qty = stock.qty - instance.qty_allocated
        else:
            stock.qty = 0
        stock.save(update_fields=['qty'])


def return_stock(instance):
    target_location = instance.from_location
    if not target_location and instance.asset:
        target_location = instance.asset.location
        
    if not target_location:
        return
        
    with transaction.atomic():
        stock, _ = ComponentStock.objects.select_for_update().get_or_create(
            component=instance.component,
            location=target_location,
            defaults={'qty': 0}
        )
        stock.qty = stock.qty + instance.qty_allocated
        stock.save(update_fields=['qty'])


@receiver(post_save, sender=ComponentAllocation)
def decrement_component_stock(sender, instance, created, **kwargs):
    if created:
        if instance.deleted_at is None:
            deduct_stock(instance)


@receiver(pre_save, sender=ComponentAllocation)
def handle_component_allocation_soft_delete(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = ComponentAllocation.all_objects.get(pk=instance.pk)
            # Soft-deleted transition
            if old_instance.deleted_at is None and instance.deleted_at is not None:
                return_stock(instance)
            # Restored transition
            elif old_instance.deleted_at is not None and instance.deleted_at is None:
                deduct_stock(instance)
        except ComponentAllocation.DoesNotExist:
            pass


@receiver(post_delete, sender=ComponentAllocation)
def increment_component_stock(sender, instance, **kwargs):
    if instance.deleted_at is None:
        return_stock(instance)
