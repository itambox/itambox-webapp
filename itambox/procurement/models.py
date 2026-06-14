from django.db import models
from django.urls import reverse
from core.models import BaseModel, ChangeLoggingMixin, SoftDeleteMixin, TaggableMixin
from core.managers import TenantScopingSoftDeleteManager
from core.currency import CurrencyField
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()

class PurchaseOrder(BaseModel, ChangeLoggingMixin, SoftDeleteMixin, TaggableMixin):
    STATUS_DRAFT = 'draft'
    STATUS_APPROVED = 'approved'
    STATUS_ORDERED = 'ordered'
    STATUS_PARTIAL = 'partial'
    STATUS_RECEIVED = 'received'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_ORDERED, 'Ordered'),
        (STATUS_PARTIAL, 'Partially Received'),
        (STATUS_RECEIVED, 'Received'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    objects = TenantScopingSoftDeleteManager()

    tenant = models.ForeignKey(
        'organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='purchase_orders'
    )
    order_number = models.CharField(max_length=100, db_index=True)
    currency = CurrencyField()
    supplier = models.ForeignKey(
        'assets.Supplier', on_delete=models.PROTECT, related_name='purchase_orders'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    order_date = models.DateField(null=True, blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    destination_location = models.ForeignKey(
        'organization.Location', on_delete=models.PROTECT, related_name='incoming_purchase_orders'
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='created_purchase_orders'
    )

    class Meta:
        permissions = [
            ("receive_purchaseorder", "Can receive stock from a purchase order"),
            ("approve_purchaseorder", "Can approve/submit a purchase order"),
        ]
        constraints = [
            models.UniqueConstraint(fields=['order_number'], condition=models.Q(deleted_at__isnull=True), name='unique_purchaseorder_number_active'),
        ]

    def __str__(self):
        return f"PO {self.order_number}"

    def get_absolute_url(self):
        return reverse('procurement:purchaseorder_detail', kwargs={'pk': self.pk})


class PurchaseOrderLine(BaseModel, ChangeLoggingMixin, SoftDeleteMixin):
    objects = TenantScopingSoftDeleteManager()

    tenant = models.ForeignKey(
        'organization.Tenant', on_delete=models.PROTECT, blank=True, null=True, related_name='po_lines'
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='lines'
    )
    asset_type = models.ForeignKey(
        'assets.AssetType', on_delete=models.PROTECT, null=True, blank=True, related_name='po_lines'
    )
    component = models.ForeignKey(
        'inventory.Component', on_delete=models.PROTECT, null=True, blank=True, related_name='po_lines'
    )
    accessory = models.ForeignKey(
        'inventory.Accessory', on_delete=models.PROTECT, null=True, blank=True, related_name='po_lines'
    )
    consumable = models.ForeignKey(
        'inventory.Consumable', on_delete=models.PROTECT, null=True, blank=True, related_name='po_lines'
    )
    license = models.ForeignKey(
        'licenses.License', on_delete=models.PROTECT, null=True, blank=True, related_name='po_lines'
    )

    qty_ordered = models.PositiveIntegerField(default=1)
    qty_received = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def __str__(self):
        item = self.asset_type or self.component or self.accessory or self.consumable or self.license or "Unknown Item"
        return f"{self.qty_ordered}x {item} for PO {self.purchase_order.order_number}"

    @property
    def currency(self):
        """Delegate to the parent PO's currency so ``{{ line.unit_price|money:line }}`` resolves correctly."""
        return self.purchase_order.currency

    @property
    def qty_outstanding(self):
        return max(0, self.qty_ordered - self.qty_received)

    @property
    def total_cost(self):
        if self.unit_price and self.qty_ordered:
            return self.unit_price * self.qty_ordered
        return None

    def clean(self):
        super().clean()
        filled = sum([
            1 if self.asset_type else 0,
            1 if self.component else 0,
            1 if self.accessory else 0,
            1 if self.consumable else 0,
            1 if self.license else 0,
        ])
        if filled == 0:
            raise ValidationError("You must specify what item you are ordering.")
        if filled > 1:
            raise ValidationError("A line item can only refer to one type of item.")


class FulfillmentLink(BaseModel, ChangeLoggingMixin, SoftDeleteMixin):
    """Links an AssetRequest to the PurchaseOrderLine that will supply it."""
    objects = TenantScopingSoftDeleteManager()
    
    tenant = models.ForeignKey(
        'organization.Tenant', on_delete=models.PROTECT,
        blank=True, null=True, related_name='fulfillment_links'
    )
    asset_request = models.ForeignKey(
        'assets.AssetRequest', on_delete=models.CASCADE,
        related_name='fulfillment_links'
    )
    purchase_order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.CASCADE,
        related_name='fulfillment_links'
    )
    qty_allocated = models.PositiveIntegerField(default=1)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['asset_request', 'purchase_order_line'],
                name='unique_request_po_line_link'
            )
        ]

