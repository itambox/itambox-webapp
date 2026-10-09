"""Per-aggregate archive service for :class:`PurchaseOrder` (#619, step 3b).

A purchase order is an aggregate root: its lines are its definition, so deleting
the order moves them with it. The approved behaviour table for this aggregate is:

* **ARCHIVE** the :class:`~procurement.models.PurchaseOrderLine` rows through
  their own ``save()``: one audited delete per row. The lines already carry a
  ``deleted_at``; the rows an operation archives share the order's own
  ``deleted_at`` instant, which is what a restore recognises them by, so a line
  removed on its own earlier is never resurrected.
* **REFUSE** while any line still has a live
  :class:`~procurement.models.FulfillmentLink`: an open asset request is being
  supplied by that line, so the order cannot disappear underneath it. The
  refusal is a typed :class:`ArchiveBlocked` carrying the blocking links, and
  nothing has been written when it is raised.
* **DETACH** the contracts that originated from the order: each is unlinked
  through its own ``save()`` with one audited update. Restore never re-attaches
  them.

No schema change: the restore marker is the shared ``deleted_at`` instant.

The whole operation runs in one transaction in the shared lock order: order,
lines, links. A failed step aborts the archive, so a refused or failed operation
leaves no partial write.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.archive_handlers import (
    ArchiveBlocked,
    ArchiveOperation,
    ArchiveResult,
    lock_aggregate_root,
)
from core.choices import ObjectChangeActionChoices
from core.managers import Scope

from .models import Contract, FulfillmentLink, PurchaseOrder, PurchaseOrderLine


def _lock_order(order: PurchaseOrder) -> PurchaseOrder:
    """Lock the order row under the ambient scope, or fail closed."""
    return lock_aggregate_root(order, noun="purchase order")


def _live_lines(order: PurchaseOrder) -> list[PurchaseOrderLine]:
    """The order's live lines, locked in a fixed (primary key) order."""
    return list(
        PurchaseOrderLine.objects.for_scope(Scope.current())
        .select_for_update()
        .filter(purchase_order=order)
        .order_by("pk")
    )


def _archived_lines(order: PurchaseOrder) -> list[PurchaseOrderLine]:
    """The lines an archive operation archived with this order.

    Those carry the order's own ``deleted_at`` instant; a line removed on its own
    carries a different one and stays removed.
    """
    # unscoped: a restore has to see the rows the archive soft-deleted, which the
    # scoped default manager hides by design.
    return list(
        PurchaseOrderLine.all_objects.for_scope(Scope.current())
        .select_for_update()
        .filter(purchase_order=order, deleted_at=order.deleted_at)
        .order_by("pk")
    )


def _blocking_links(lines: list[PurchaseOrderLine]) -> list[FulfillmentLink]:
    """Live fulfillment links that still depend on one of ``lines``."""
    if not lines:
        return []
    return list(
        FulfillmentLink.objects.for_scope(Scope.current())
        .select_for_update()
        .filter(purchase_order_line__in=lines)
        .order_by("pk")
    )


def _assert_no_open_links(order: PurchaseOrder, links: list[FulfillmentLink]) -> None:
    """Refuse while a line is still supplying an asset request."""
    if links:
        raise ArchiveBlocked(
            _("Cannot delete %(object)s: %(count)d asset request fulfillment link(s) still depend on its lines.")
            % {"object": str(order), "count": len(links)},
            blockers=links,
        )


def _archive_lines(lines: list[PurchaseOrderLine], when, operation: ArchiveOperation) -> int:
    """Archive the order's lines with the order's own ``deleted_at`` instant."""
    message = operation.archive_message()
    for line in lines:
        line.deleted_at = when
        line._changelog_message = message
        line._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        line.save(update_fields=["deleted_at"])
    return len(lines)


def _detach_contracts(order: PurchaseOrder, operation: ArchiveOperation) -> int:
    """Unlink the contracts that originated from this order, one audit entry each."""
    contracts = list(Contract.objects.for_scope(Scope.current()).filter(purchase_order=order).order_by("pk"))
    for contract in contracts:
        contract.purchase_order = None
        contract._changelog_message = operation.detach_message()
        contract.save(update_fields=["purchase_order"])
    return len(contracts)


def _restore_lines(lines: list[PurchaseOrderLine]) -> None:
    """Bring the archived lines back."""
    for line in lines:
        line.deleted_at = None
        line.save(update_fields=["deleted_at"])


def archive_purchase_order(order: PurchaseOrder, *, actor=None, request=None) -> ArchiveResult:
    """Archive one purchase order with its lines, or refuse.

    An already-archived order is a no-op (``archived=0``): ``DELETE`` is
    idempotent for the caller, and the row lock makes a concurrent double archive
    safe instead of double-auditing.

    :param actor: the authenticated principal (the audit rows follow the
        request/task context).
    :param request: the originating request, when there is one.
    :raises ArchiveBlocked: a line still has a live fulfillment link.
    """
    with transaction.atomic():
        locked = _lock_order(order)
        if locked.deleted_at is not None:
            return ArchiveResult()

        lines = _live_lines(locked)
        _assert_no_open_links(locked, _blocking_links(lines))
        operation = ArchiveOperation.begin(locked)

        when = timezone.now()
        archived_lines = _archive_lines(lines, when, operation)
        detached = _detach_contracts(locked, operation)

        locked.deleted_at = when
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(archived=1 + archived_lines, detached=detached, operation_id=operation.id)


def restore_purchase_order(order: PurchaseOrder, *, actor=None, request=None) -> None:
    """Bring an archived purchase order back with its archived lines, or refuse.

    The active order-number slot is revalidated (an order created in the meantime
    with the same number). Detached contracts are not re-attached.

    :param actor: the authenticated principal (see :func:`archive_purchase_order`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_order(order)
        if locked.deleted_at is None:
            return

        conflict = PurchaseOrder.objects.for_scope(Scope.current()).filter(order_number=locked.order_number).exists()
        if conflict:
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: another active purchase order already uses the number '%(number)s'.")
                % {"object": str(locked), "number": locked.order_number}
            )

        lines = _archived_lines(locked)
        _restore_lines(lines)
        locked.restore()
