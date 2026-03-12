from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from .models import AccessoryAssignment, ConsumableAssignment, AccessoryStock, ConsumableStock


def checkout_accessory(accessory, qty, holder=None, location=None, asset=None, user=None, notes="", source_location=None, request=None, **kwargs):
    if not accessory.allow_overallocate and accessory.available < qty:
        raise ValidationError("No stock available for checkout.")
    if not holder and not location and not asset:
        raise ValidationError("Either holder, location, or asset must be specified.")
    if source_location:
        loc_stock = AccessoryStock.objects.filter(
            accessory=accessory, location=source_location
        ).aggregate(qty=Sum('qty'))['qty'] or 0
        if not accessory.allow_overallocate and loc_stock < qty:
            raise ValidationError(
                f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
            )

    with transaction.atomic():
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


def checkin_accessory(assignment_pk, user=None):
    assignment = get_object_or_404(AccessoryAssignment, pk=assignment_pk)
    accessory = assignment.accessory
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location or assignment.assigned_asset

    assignment.delete()
    return accessory, qty, recipient


def checkout_consumable(consumable, qty, holder=None, location=None, asset=None, user=None, notes="", source_location=None, request=None, **kwargs):
    if not consumable.allow_overallocate and consumable.available < qty:
        raise ValidationError("No stock available for consumption checkout.")
    if not holder and not location and not asset:
        raise ValidationError("Either holder, location, or asset must be specified.")
    if source_location:
        loc_stock = ConsumableStock.objects.filter(
            consumable=consumable, location=source_location
        ).aggregate(qty=Sum('qty'))['qty'] or 0
        if not consumable.allow_overallocate and loc_stock < qty:
            raise ValidationError(
                f"Insufficient stock at {source_location}. Available: {loc_stock}, Requested: {qty}"
            )

    with transaction.atomic():
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
