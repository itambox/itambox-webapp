from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from typing import Tuple, Any, Optional
from .models import AccessoryAssignment, ConsumableAssignment, ComponentAllocation, AccessoryStock, ConsumableStock, ComponentStock


def checkout_inventory_item(
    item: Any,
    qty: int,
    holder: Optional[Any] = None,
    location: Optional[Any] = None,
    asset: Optional[Any] = None,
    user: Optional[Any] = None,
    notes: str = "",
    source_location: Optional[Any] = None,
    request: Optional[Any] = None,
    **kwargs: Any
) -> Any:
    if not holder and not location and not asset:
        raise ValidationError("Either holder, location, or asset must be specified.")

    item_class_name = item.__class__.__name__
    if item_class_name == 'Accessory':
        assignment_model = AccessoryAssignment
        stock_model = AccessoryStock
        item_field = 'accessory'
    elif item_class_name == 'Consumable':
        assignment_model = ConsumableAssignment
        stock_model = ConsumableStock
        item_field = 'consumable'
    else:
        assignment_model = ComponentAllocation
        stock_model = ComponentStock
        item_field = 'component'

    with transaction.atomic():
        # Lock the row to prevent concurrent overallocation
        item = type(item).objects.select_for_update().get(pk=item.pk)

        if not item.allow_overallocate and item.available < qty:
            raise ValidationError("No stock available for checkout.")
            
        if source_location:
            loc_stock = stock_model.objects.filter(
                **{item_field: item, 'location': source_location}
            ).aggregate(qty=Sum('qty'))['qty'] or 0
            if not item.allow_overallocate and loc_stock < qty:
                raise ValidationError(
                    f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
                )

        assignment = assignment_model.objects.create(
            assigned_holder=holder,
            assigned_location=location,
            assigned_asset=asset,
            from_location=source_location,
            qty=qty,
            notes=notes,
            **{item_field: item}
        )
    return assignment


def checkin_accessory(assignment_pk: Any, user: Optional[Any] = None) -> Tuple[Any, int, Any]:
    assignment = get_object_or_404(AccessoryAssignment, pk=assignment_pk)
    accessory = assignment.accessory
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location or assignment.assigned_asset

    assignment.delete()
    return accessory, qty, recipient


def checkin_component(assignment_pk: Any, user: Optional[Any] = None) -> Tuple[Any, int, Any]:
    assignment = get_object_or_404(ComponentAllocation, pk=assignment_pk)
    component = assignment.component
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location or assignment.assigned_asset

    assignment.delete()
    return component, qty, recipient


def adjust_inventory_stock(
    assignment_instance: Any,
    is_delete: bool = False,
    old_instance: Optional[Any] = None
) -> None:
    """
    Unified stock adjustment logic for ComponentAllocation, AccessoryAssignment, and ConsumableAssignment.
    """
    from django.db import transaction
    from django.core.exceptions import ValidationError
    
    if hasattr(assignment_instance, 'accessory'):
        item_field = 'accessory'
        from .models import AccessoryStock as StockModel
    elif hasattr(assignment_instance, 'consumable'):
        item_field = 'consumable'
        from .models import ConsumableStock as StockModel
    elif hasattr(assignment_instance, 'component'):
        item_field = 'component'
        from .models import ComponentStock as StockModel
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
        # No max(0, ...) clamp: clamping the deduction while restoring the full qty
        # on check-in materialises stock out of nothing. The signed `qty` field lets
        # over-allocation go negative so deduction and restoration stay symmetric.
        stock.qty = stock.qty + qty_diff
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
                    old_instance = assignment_instance.__class__._base_manager.get(pk=assignment_instance.pk)
                
                old_deleted = getattr(old_instance, 'deleted_at', None) is not None
                new_deleted = getattr(assignment_instance, 'deleted_at', None) is not None
                
                if old_deleted and not new_deleted:
                    # Restoring a soft-deleted assignment: only apply new stock allocation
                    if assignment_instance.from_location:
                        update_stock(item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate)
                elif not old_deleted and new_deleted:
                    # Soft-deleting: delete() already reverted the stock, do nothing here
                    pass
                elif not old_deleted and not new_deleted:
                    # Normal update: revert old allocation, apply new allocation
                    if old_instance.from_location:
                        old_item = getattr(old_instance, item_field)
                        update_stock(old_item, old_instance.from_location, old_instance.qty, old_item.allow_overallocate)
                    if assignment_instance.from_location:
                        update_stock(item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate)
