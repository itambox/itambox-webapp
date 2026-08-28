"""Stock bookkeeping required by inventory assignment model hooks.

This module is a model-support leaf.  It resolves concrete stock models through
the app registry so ``inventory.models`` can import the bookkeeping function at
module scope without depending on the inventory service layer.
"""

from typing import Any, Optional

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models_assignment_write import assignment_write_is_permitted


def require_authorized_assignment_creation(assignment_instance: Any, is_delete: bool) -> None:
    """Refuse an unpermitted assignment write before stock is touched."""
    if not assignment_write_is_permitted(assignment_instance):
        raise ValidationError(_("Assignments must be mutated through the authorized inventory service."))


def _update_stock(stock_model, item_field, item, location, qty_diff, allow_overallocate):
    """Apply one signed quantity change to the locked owner pool."""
    stock, _created = stock_model._base_manager.select_for_update().get_or_create(
        location=location,
        **{item_field: item},
        defaults={"qty": 0},
    )
    if qty_diff < 0 and not allow_overallocate and stock.qty < abs(qty_diff):
        raise ValidationError(
            _("Insufficient stock at %(location)s. Available: %(available)s, Requested: %(requested)s")
            % {"location": location, "available": stock.qty, "requested": abs(qty_diff)}
        )
    stock.qty = stock.qty + qty_diff
    stock.save(update_fields=["qty"])


def _stock_model_for(assignment_instance):
    item_field = getattr(assignment_instance, "_item_attr", None)
    stock_model_label = getattr(assignment_instance, "_stock_model_label", None)
    if not item_field or not stock_model_label:
        raise ValueError("Unknown assignment type for stock adjustment.")
    return item_field, apps.get_model(stock_model_label)


def _restore_deleted_assignment(assignment_instance, stock_model, item_field):
    if assignment_instance.from_location:
        item = getattr(assignment_instance, item_field)
        _update_stock(
            stock_model,
            item_field,
            item,
            assignment_instance.from_location,
            assignment_instance.qty,
            item.allow_overallocate,
        )


def _deduct_new_assignment(assignment_instance, stock_model, item_field):
    if assignment_instance.from_location:
        item = getattr(assignment_instance, item_field)
        _update_stock(
            stock_model,
            item_field,
            item,
            assignment_instance.from_location,
            -assignment_instance.qty,
            item.allow_overallocate,
        )


def _adjust_existing_assignment(assignment_instance, stock_model, item_field, old_instance=None):
    if old_instance is None:
        old_instance = assignment_instance.__class__._base_manager.get(pk=assignment_instance.pk)

    old_deleted = getattr(old_instance, "deleted_at", None) is not None
    new_deleted = getattr(assignment_instance, "deleted_at", None) is not None
    if old_deleted and not new_deleted:
        _deduct_new_assignment(assignment_instance, stock_model, item_field)
    elif not old_deleted and new_deleted:
        # delete() already restored the source pool before the soft delete.
        return
    elif not old_deleted and not new_deleted:
        _restore_deleted_assignment(old_instance, stock_model, item_field)
        _deduct_new_assignment(assignment_instance, stock_model, item_field)


def adjust_inventory_stock(
    assignment_instance: Any, is_delete: bool = False, old_instance: Optional[Any] = None
) -> None:
    """Adjust stock for an inventory assignment create, update, restore, or delete."""
    require_authorized_assignment_creation(assignment_instance, is_delete)
    item_field, stock_model = _stock_model_for(assignment_instance)

    with transaction.atomic():
        if is_delete:
            _restore_deleted_assignment(assignment_instance, stock_model, item_field)
        elif assignment_instance.pk is None:
            _deduct_new_assignment(assignment_instance, stock_model, item_field)
        else:
            _adjust_existing_assignment(assignment_instance, stock_model, item_field, old_instance)
