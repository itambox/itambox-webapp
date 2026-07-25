"""Stock bookkeeping for inventory assignments -- a runtime leaf (issue #87, phase D).

``AccessoryAssignment`` / ``ComponentAllocation`` / ``ConsumableAssignment``
adjust their source pool from ``save()`` and ``delete()``, so the model layer
needs this routine at import time. Keeping it in ``inventory.services`` -- which
imports ``inventory.models`` at module scope for the checkout/check-in flows --
made that a cycle, deferred behind a function-body import in six model methods.

This module resolves the stock model through the app registry using the
``_item_attr`` / ``_stock_model_label`` hooks already declared on
``AbstractAssignment``, so it imports nothing first-party and
``inventory.models`` can depend on it at module scope. ``inventory.services``
re-exports ``adjust_inventory_stock`` so the published call path is unchanged.
"""

from typing import Any, Optional

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _


def adjust_inventory_stock(
    assignment_instance: Any, is_delete: bool = False, old_instance: Optional[Any] = None
) -> None:
    """
    Unified stock adjustment logic for ComponentAllocation, AccessoryAssignment, and ConsumableAssignment.
    """
    item_field = getattr(assignment_instance, "_item_attr", None)
    stock_model_label = getattr(assignment_instance, "_stock_model_label", None)
    if not item_field or not stock_model_label:
        raise ValueError("Unknown assignment type for stock adjustment.")
    # App registry rather than a direct import: that is what keeps this module a
    # leaf and the inventory.models -> inventory.services cycle closed for good.
    StockModel = apps.get_model(stock_model_label)

    item = getattr(assignment_instance, item_field)

    def update_stock(item_val, location, qty_diff, allow_overallocate):
        # _base_manager: with pool ownership on stock.tenant (phase 4), a
        # granted cross-tenant checkout must adjust the OWNER's pool, which
        # the grantee's scoped manager cannot see (a scoped get_or_create
        # would try to create a duplicate and hit the unique constraint).
        # Authorization happened upstream (resolve_grant_for_checkout +
        # AbstractAssignment.clean); this is pure bookkeeping.
        stock, _created = StockModel._base_manager.select_for_update().get_or_create(
            location=location, **{item_field: item_val}, defaults={"qty": 0}
        )
        if qty_diff < 0:  # Deducting stock
            if not allow_overallocate and stock.qty < abs(qty_diff):
                raise ValidationError(
                    _("Insufficient stock at %(location)s. Available: %(available)s, Requested: %(requested)s")
                    % {"location": location, "available": stock.qty, "requested": abs(qty_diff)}
                )
        # No max(0, ...) clamp: clamping the deduction while restoring the full qty
        # on check-in materialises stock out of nothing. The signed `qty` field lets
        # over-allocation go negative so deduction and restoration stay symmetric.
        stock.qty = stock.qty + qty_diff
        stock.save(update_fields=["qty"])

    with transaction.atomic():
        if is_delete:
            if assignment_instance.from_location:
                update_stock(item, assignment_instance.from_location, assignment_instance.qty, item.allow_overallocate)
        else:
            is_new = assignment_instance.pk is None
            if is_new:
                if assignment_instance.from_location:
                    update_stock(
                        item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate
                    )
            else:
                if old_instance is None:
                    old_instance = assignment_instance.__class__._base_manager.get(pk=assignment_instance.pk)

                old_deleted = getattr(old_instance, "deleted_at", None) is not None
                new_deleted = getattr(assignment_instance, "deleted_at", None) is not None

                if old_deleted and not new_deleted:
                    # Restoring a soft-deleted assignment: only apply new stock allocation
                    if assignment_instance.from_location:
                        update_stock(
                            item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate
                        )
                elif not old_deleted and new_deleted:
                    # Soft-deleting: delete() already reverted the stock, do nothing here
                    pass
                elif not old_deleted and not new_deleted:
                    # Normal update: revert old allocation, apply new allocation
                    if old_instance.from_location:
                        old_item = getattr(old_instance, item_field)
                        update_stock(
                            old_item, old_instance.from_location, old_instance.qty, old_item.allow_overallocate
                        )
                    if assignment_instance.from_location:
                        update_stock(
                            item, assignment_instance.from_location, -assignment_instance.qty, item.allow_overallocate
                        )
