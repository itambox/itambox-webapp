from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from core.models import BaseModel, ChangeLoggingMixin, DeletableVaultModel
from core.mixins import TaggableMixin, AutoSlugMixin, SoftDeleteMixin, JournalingMixin, SubscribableMixin, CustomFieldDataMixin


from .mixins import CheckableInventoryModelMixin


class AbstractInventoryItem(CustomFieldDataMixin, CheckableInventoryModelMixin, AutoSlugMixin, SubscribableMixin, DeletableVaultModel):
    allow_global_tenant = True
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    manufacturer = models.ForeignKey(
        'assets.Manufacturer',
        on_delete=models.PROTECT,
        related_name='%(class)ss'
    )
    category = models.ForeignKey(
        'assets.Category',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='%(class)ss',
        db_index=True
    )
    supplier = models.ForeignKey(
        'assets.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supplier_%(class)ss',
        verbose_name=_("Supplier"),
        db_index=True
    )
    part_number = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_("SKU or manufacturer part number")
    )
    min_qty = models.PositiveIntegerField(
        default=0,
        blank=True,
        verbose_name=_("Safety Threshold"),
        help_text=_("Alert threshold quantity")
    )
    allow_overallocate = models.BooleanField(
        default=False,
        verbose_name=_("Allow Over-allocation"),
        help_text=_("Allow checkout count to exceed stock capacity")
    )
    notes = models.TextField(blank=True)
    tenant = models.ForeignKey(
        'organization.Tenant',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='%(class)ss',
        db_index=True
    )
    tags = models.ManyToManyField(
        'extras.Tag',
        related_name='%(app_label)s_%(class)s',
        blank=True
    )

    slug_source = ('manufacturer__name', 'name')

    class Meta:
        abstract = True
        ordering = ('manufacturer', 'name')
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(deleted_at__isnull=True), name='unique_%(class)s_slug_active'),
        ]

    def __str__(self):
        return f"{self.manufacturer.name} {self.name}"


class AbstractStock(ChangeLoggingMixin, BaseModel):
    location = models.ForeignKey(
        'organization.Location',
        on_delete=models.PROTECT,
        related_name='%(class)s_stocks',
        db_index=True
    )
    qty = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ('location',)


class AbstractAssignment(JournalingMixin, TaggableMixin, SoftDeleteMixin, ChangeLoggingMixin, BaseModel):
    assigned_holder = models.ForeignKey(
        'organization.AssetHolder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        db_index=True
    )
    assigned_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        db_index=True
    )
    assigned_asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_assignments',
        db_index=True
    )
    from_location = models.ForeignKey(
        'organization.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_checkouts',
        verbose_name=_("From Location"),
        db_index=True
    )
    qty = models.PositiveIntegerField(default=1, verbose_name=_("Checkout Quantity"))
    assigned_date = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    tags = models.ManyToManyField('extras.Tag', related_name='%(class)s_assignments', blank=True)

    class Meta:
        abstract = True
        ordering = ('-assigned_date',)

    def __str__(self):
        recipient = self.assigned_holder or self.assigned_location or self.assigned_asset or "Unknown"
        return f"{self.qty}x assigned to {recipient}"
