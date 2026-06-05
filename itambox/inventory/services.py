from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from typing import Tuple, Any, Optional
from .models import AccessoryAssignment, ConsumableAssignment, AccessoryStock, ConsumableStock


def checkout_accessory(
    accessory: Any,
    qty: int,
    holder: Optional[Any] = None,
    location: Optional[Any] = None,
    asset: Optional[Any] = None,
    user: Optional[Any] = None,
    notes: str = "",
    source_location: Optional[Any] = None,
    request: Optional[Any] = None,
    **kwargs: Any
) -> AccessoryAssignment:
    if not holder and not location and not asset:
        raise ValidationError("Either holder, location, or asset must be specified.")

    with transaction.atomic():
        # Lock the accessory row to prevent concurrent overallocation
        accessory = type(accessory).objects.select_for_update().get(pk=accessory.pk)

        if not accessory.allow_overallocate and accessory.available < qty:
            raise ValidationError("No stock available for checkout.")
            
        if source_location:
            loc_stock = AccessoryStock.objects.filter(
                accessory=accessory, location=source_location
            ).aggregate(qty=Sum('qty'))['qty'] or 0
            if not accessory.allow_overallocate and loc_stock < qty:
                raise ValidationError(
                    f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
                )

        assignment = AccessoryAssignment.objects.create(
            accessory=accessory,
            assigned_holder=holder,
            assigned_location=location,
            assigned_asset=asset,
            from_location=source_location,
            qty=qty,
            notes=notes
        )
    return assignment


def checkin_accessory(assignment_pk: Any, user: Optional[Any] = None) -> Tuple[Any, int, Any]:
    assignment = get_object_or_404(AccessoryAssignment, pk=assignment_pk)
    accessory = assignment.accessory
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location or assignment.assigned_asset

    assignment.delete()
    return accessory, qty, recipient


def checkout_consumable(
    consumable: Any,
    qty: int,
    holder: Optional[Any] = None,
    location: Optional[Any] = None,
    asset: Optional[Any] = None,
    user: Optional[Any] = None,
    notes: str = "",
    source_location: Optional[Any] = None,
    request: Optional[Any] = None,
    **kwargs: Any
) -> ConsumableAssignment:
    if not holder and not location and not asset:
        raise ValidationError("Either holder, location, or asset must be specified.")

    with transaction.atomic():
        # Lock the consumable row to prevent concurrent overallocation
        consumable = type(consumable).objects.select_for_update().get(pk=consumable.pk)

        if not consumable.allow_overallocate and consumable.available < qty:
            raise ValidationError("No stock available for consumption checkout.")
            
        if source_location:
            loc_stock = ConsumableStock.objects.filter(
                consumable=consumable, location=source_location
            ).aggregate(qty=Sum('qty'))['qty'] or 0
            if not consumable.allow_overallocate and loc_stock < qty:
                raise ValidationError(
                    f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
                )

        assignment = ConsumableAssignment.objects.create(
            consumable=consumable,
            assigned_holder=holder,
            assigned_location=location,
            assigned_asset=asset,
            from_location=source_location,
            qty=qty,
            notes=notes
        )
    return assignment


def adjust_inventory_stock(
    assignment_instance: Any,
    is_delete: bool = False,
    old_instance: Optional[Any] = None
) -> None:
    """
    Unified stock adjustment logic for AccessoryAssignment and ConsumableAssignment.
    """
    from django.db import transaction
    from django.core.exceptions import ValidationError
    
    if hasattr(assignment_instance, 'accessory'):
        item_field = 'accessory'
        from .models import AccessoryStock as StockModel
    elif hasattr(assignment_instance, 'consumable'):
        item_field = 'consumable'
        from .models import ConsumableStock as StockModel
    else:
        raise ValueError("Unknown assignment type for stock adjustment.")

    item = getattr(assignment_instance, item_field)
    
    def update_stock(item_val, location, qty_diff, allow_overallocate):
        stock, _ = StockModel.objects.select_for_update().get_or_create(
            location=location,
            **{item_field: item_val},
            defaults={'qty': 0}
        )
        if qty_diff < 0: # Deducting stock
            if not allow_overallocate and stock.qty < abs(qty_diff):
                raise ValidationError(
                    f"Insufficient stock at {location}. Available: {stock.qty}, Requested: {abs(qty_diff)}"
                )
        stock.qty = max(0, stock.qty + qty_diff)
        stock.save(update_fields=['qty'])

    with transaction.atomic():
        if is_delete:
            if assignment_instance.from_location:
                update_stock(item, assignment_instance.from_location, assignment_instance.qty, item.allow_overallocate)
        else:
            is_new = assignment_instance.pk is None
            if is_new:
                if assignment_instance.from_location:
                    update_stock(item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate)
            else:
                if old_instance is None:
                    old_instance = assignment_instance.__class__.objects.get(pk=assignment_instance.pk)
                
                # Revert old stock allocation
                if old_instance.from_location:
                    old_item = getattr(old_instance, item_field)
                    update_stock(old_item, old_instance.from_location, old_instance.qty, old_item.allow_overallocate)
                
                # Apply new stock allocation
                if assignment_instance.from_location:
                    update_stock(item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate)

