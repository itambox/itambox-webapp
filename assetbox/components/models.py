from django.db import models
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin
from core.mixins import TaggableMixin, AutoSlugMixin, JournalingMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, SoftDeleteMixin
from core.managers import SoftDeleteManager, AllObjectsManager


class Component(AutoSlugMixin, JournalingMixin, TaggableMixin, ImageAttachmentMixin, CloneableMixin, ExportableMixin, ChangeLoggingMixin, BaseModel):
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
        return reverse('assets:component_detail', kwargs={'pk': self.pk})

    @property
    def total_stock(self):
        return self.stocks.aggregate(total=models.Sum('qty'))['total'] or 0

    @property
    def total_allocated(self):
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


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=ComponentAllocation)
def decrement_component_stock(sender, instance, created, **kwargs):
    if not created:
        return
    if not instance.asset or not instance.asset.location:
        return
    stock, _ = ComponentStock.objects.get_or_create(
        component=instance.component,
        location=instance.asset.location,
        defaults={'qty': 0}
    )
    stock.qty = max(0, stock.qty - instance.qty_allocated)
    stock.save(update_fields=['qty'])


@receiver(post_delete, sender=ComponentAllocation)
def increment_component_stock(sender, instance, **kwargs):
    if not instance.asset or not instance.asset.location:
        return
    stock, _ = ComponentStock.objects.get_or_create(
        component=instance.component,
        location=instance.asset.location,
        defaults={'qty': 0}
    )
    stock.qty = stock.qty + instance.qty_allocated
    stock.save(update_fields=['qty'])
