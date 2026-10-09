"""Per-aggregate archive service for the inventory items (#619, step 4).

:class:`~inventory.models.Component`, :class:`~inventory.models.Accessory` and
:class:`~inventory.models.Consumable` are aggregate roots with one identical
shape, so they share one behaviour table and one service rather than three
copies. The approved behaviour table for each of them is:

* **REFUSE** while a live assignment draws on the item (an open checkout still
  has to come back), while a stock row still carries a positive quantity (the
  pool is not the item's to throw away), or while a live kit item lists it (an
  active kit still needs the part its line promises). The refusal is a typed
  :class:`ArchiveBlocked` carrying the blocking counts, and nothing has been
  written when it is raised.
* **ARCHIVE** the empty stock rows through their own ``save()`` (leaf
  ``deleted_at`` plus the operation marker, one audited delete per row); a
  restore brings back exactly the rows this operation archived.
* **DETACH** the open asset requests and the subscriptions covering the item.
  An item-only request cannot lose its item link (the request must name exactly
  one category), so detaching it means closing it: the request is cancelled
  through its own ``save()`` (one audited update each) and keeps naming the
  item as evidence. Each covering subscription assignment is ended through its
  own ``delete()``. Restore never re-attaches or reopens either.
* **KEEP** the closed asset requests, the consumed assignments, the purchase
  order lines, and the journal entries and attachments: they are evidence that
  keeps referencing the archived item, which stays resolvable through
  ``all_objects``.

No schema change: the stock rows already carry the leaf ``deleted_at`` and the
operation marker from step 3c; nothing else is archived.

The whole operation runs in one transaction: the item row is locked first, then
the environment is read, then the rows are written. A child failure aborts the
archive, so a refused or failed operation leaves no partial write.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Model, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest
from core.archive_handlers import (
    ArchiveBehaviour,
    ArchiveBlocked,
    ArchiveOperation,
    ArchiveRelation,
    ArchiveResult,
    lock_aggregate_root,
)
from core.choices import ObjectChangeActionChoices
from core.managers import Scope
from procurement.models import PurchaseOrderLine
from subscriptions.models import SubscriptionAssignment

from .models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
    KitItem,
)

_OPEN_REQUEST_STATUSES = (
    RequestStatusChoices.PENDING,
    RequestStatusChoices.APPROVED,
    RequestStatusChoices.PROCUREMENT,
)


@dataclass(frozen=True)
class InventoryItemAggregate:
    """One inventory item family: the root and the two models that hang off it."""

    model: type[Model]
    noun: str
    item_attr: str
    stock_model: type[Model]
    assignment_model: type[Model]

    @property
    def assignment_key(self) -> str:
        return f"inventory.{self.assignment_model._meta.model_name}.{self.item_attr}"

    @property
    def stock_key(self) -> str:
        return f"inventory.{self.stock_model._meta.model_name}.{self.item_attr}"


COMPONENT = InventoryItemAggregate(Component, "component", "component", ComponentStock, ComponentAllocation)
ACCESSORY = InventoryItemAggregate(Accessory, "accessory", "accessory", AccessoryStock, AccessoryAssignment)
CONSUMABLE = InventoryItemAggregate(Consumable, "consumable", "consumable", ConsumableStock, ConsumableAssignment)

AGGREGATES: tuple[InventoryItemAggregate, ...] = (COMPONENT, ACCESSORY, CONSUMABLE)


def archive_relations(aggregate: InventoryItemAggregate) -> tuple[ArchiveRelation, ...]:
    """The reviewed behaviour table of one inventory item aggregate."""
    item = aggregate.item_attr
    return (
        ArchiveRelation(aggregate.assignment_key, ArchiveBehaviour.REFUSE, "while open; consumed rows KEEP"),
        ArchiveRelation(aggregate.stock_key, ArchiveBehaviour.REFUSE, "while qty > 0; empty rows ARCHIVE"),
        ArchiveRelation(f"inventory.kititem.{item}", ArchiveBehaviour.REFUSE, "while a live kit lists it"),
        ArchiveRelation(
            f"procurement.purchaseorderline.{item}", ArchiveBehaviour.KEEP, "purchase order lines stay as evidence"
        ),
        ArchiveRelation(
            f"assets.assetrequest.{item}", ArchiveBehaviour.DETACH, "open requests are cancelled; closed rows KEEP"
        ),
        ArchiveRelation(
            "subscriptions", ArchiveBehaviour.DETACH, "subscriptions covering the item are ended; no re-attach"
        ),
        ArchiveRelation("journal_entries", ArchiveBehaviour.KEEP, "journal entries stay as evidence"),
        ArchiveRelation("file_attachments", ArchiveBehaviour.KEEP, "attachments stay as evidence"),
        ArchiveRelation("image_attachments", ArchiveBehaviour.KEEP, "attachments stay as evidence"),
    )


def _scoped(model) -> QuerySet:
    return model.objects.for_scope(Scope.current())


def _item_field(aggregate: InventoryItemAggregate, item) -> dict[str, Any]:
    return {aggregate.item_attr: item}


def _blocker_counts(aggregate: InventoryItemAggregate, item) -> list[tuple[str, int]]:
    """``(label, count)`` for every REFUSE condition that currently holds."""
    checks = [
        (_("open assignments"), _scoped(aggregate.assignment_model).filter(**_item_field(aggregate, item)).count()),
        (
            _("stock rows with remaining quantity"),
            _scoped(aggregate.stock_model).filter(**_item_field(aggregate, item), qty__gt=0).count(),
        ),
        (_("live kit items"), _scoped(KitItem).filter(**_item_field(aggregate, item)).count()),
    ]
    return [(label, count) for label, count in checks if count]


def _refusal(item, blockers: Sequence[tuple[str, int]]) -> ArchiveBlocked:
    total = sum(count for _label, count in blockers)
    detail = ", ".join(f"{count} {label}" for label, count in blockers)
    headline = ngettext(
        "Cannot delete %(object)s: %(count)s dependent record must be resolved first (%(detail)s).",
        "Cannot delete %(object)s: %(count)s dependent records must be resolved first (%(detail)s).",
        total,
    ) % {"object": str(item), "count": total, "detail": detail}
    return ArchiveBlocked(headline, blockers=blockers)


def _empty_stock_rows(aggregate: InventoryItemAggregate, item) -> list:
    """The item's stock rows, locked in a fixed (primary key) order."""
    return list(
        _scoped(aggregate.stock_model).select_for_update().filter(**_item_field(aggregate, item)).order_by("pk")
    )


def _archive_stock(aggregate: InventoryItemAggregate, item, when, operation: ArchiveOperation) -> int:
    """Archive the item's stock rows with the operation's marker."""
    rows = _empty_stock_rows(aggregate, item)
    message = operation.archive_message()
    for row in rows:
        row.deleted_at = when
        row.archive_operation_id = operation.id
        row._changelog_message = message
        row._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        row.save(update_fields=["deleted_at", "archive_operation_id"])
    return len(rows)


def _detach_open_requests(aggregate: InventoryItemAggregate, item, operation: ArchiveOperation) -> int:
    """Close the open asset requests that name the item, one audit entry each.

    The request keeps its item link (a request names exactly one category), so
    detaching it from the archived item means cancelling it.
    """
    requests = _scoped(AssetRequest).filter(status__in=_OPEN_REQUEST_STATUSES, **_item_field(aggregate, item))
    detached = 0
    for request in requests.select_for_update().order_by("pk"):
        request.status = RequestStatusChoices.CANCELLED
        request._changelog_message = operation.detach_message()
        request.save(update_fields=["status"])
        detached += 1
    return detached


def _end_subscription_assignments(item, operation: ArchiveOperation) -> int:
    """End the subscriptions covering the item, one audited delete each."""
    content_type = ContentType.objects.get_for_model(type(item))
    assignments = _scoped(SubscriptionAssignment).filter(content_type=content_type, object_id=item.pk)
    ended = 0
    for assignment in assignments:
        assignment._changelog_message = operation.detach_message()
        assignment.delete()
        ended += 1
    return ended


def _kept_evidence_count(aggregate: InventoryItemAggregate, item) -> int:
    """Evidence rows that stay attached to the archived item."""
    closed_requests = (
        _scoped(AssetRequest).exclude(status__in=_OPEN_REQUEST_STATUSES).filter(**_item_field(aggregate, item)).count()
    )
    # unscoped: consumed assignments are soft-deleted tombstones the scoped default
    # manager hides by design; the scope is applied explicitly.
    consumed_assignments = (
        aggregate.assignment_model.all_objects.for_scope(Scope.current())
        .filter(**_item_field(aggregate, item), deleted_at__isnull=False)
        .count()
    )
    purchase_order_lines = _scoped(PurchaseOrderLine).filter(**_item_field(aggregate, item)).count()
    return (
        closed_requests
        + consumed_assignments
        + purchase_order_lines
        + item.journal_entries.count()
        + item.file_attachments.count()
        + item.image_attachments.count()
    )


def _restore_stock(aggregate: InventoryItemAggregate, item) -> int:
    """Restore the stock rows that were archived with the item.

    A row deleted on its own (a leaf delete, or an archive of its location)
    carries a different ``deleted_at`` instant and no marker of this operation,
    so it is never resurrected here.
    """
    # unscoped: a restore has to see the stock the archive soft-deleted, which the
    # scoped default manager hides by design; the scope is applied explicitly.
    rows = (
        aggregate.stock_model.all_objects.for_scope(Scope.current())
        .select_for_update()
        .filter(**_item_field(aggregate, item), deleted_at=item.deleted_at, archive_operation_id__isnull=False)
        .order_by("pk")
    )
    restored = 0
    for row in rows:
        row.deleted_at = None
        row.archive_operation_id = None
        row.save(update_fields=["deleted_at", "archive_operation_id"])
        restored += 1
    return restored


def _archive_item(aggregate: InventoryItemAggregate, item, *, actor=None, request=None) -> ArchiveResult:
    """Archive one inventory item with its empty stock rows, or refuse."""
    with transaction.atomic():
        locked = lock_aggregate_root(item, noun=aggregate.noun)
        if locked.deleted_at is not None:
            return ArchiveResult()

        blockers = _blocker_counts(aggregate, locked)
        if blockers:
            raise _refusal(locked, blockers)

        operation = ArchiveOperation.begin(locked)
        when = timezone.now()
        archived_stock = _archive_stock(aggregate, locked, when, operation)
        detached = _detach_open_requests(aggregate, locked, operation) + _end_subscription_assignments(
            locked, operation
        )
        kept = _kept_evidence_count(aggregate, locked)

        locked.deleted_at = when
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(
        archived=1 + archived_stock,
        detached=detached,
        kept=kept,
        operation_id=operation.id,
    )


def _restore_item(aggregate: InventoryItemAggregate, item, *, actor=None, request=None) -> None:
    """Bring an archived inventory item back with the stock rows its archive moved."""
    with transaction.atomic():
        locked = lock_aggregate_root(item, noun=aggregate.noun)
        if locked.deleted_at is None:
            return

        conflict = _scoped(aggregate.model).filter(slug=locked.slug).exists()
        if conflict:
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: another active item already uses the slug '%(slug)s'.")
                % {"object": str(locked), "slug": locked.slug}
            )

        _restore_stock(aggregate, locked)
        locked.restore()


def _make_archive_handler(aggregate: InventoryItemAggregate) -> Callable[..., ArchiveResult]:
    def archive(item, *, actor=None, request=None) -> ArchiveResult:
        return _archive_item(aggregate, item, actor=actor, request=request)

    return archive


def _make_restore_handler(aggregate: InventoryItemAggregate) -> Callable[..., None]:
    def restore(item, *, actor=None, request=None) -> None:
        return _restore_item(aggregate, item, actor=actor, request=request)

    return restore


#: ``model label -> (archive handler, restore handler)`` for the app registration.
HANDLERS: dict[str, tuple[Callable[..., ArchiveResult], Callable[..., None]]] = {
    aggregate.model._meta.label_lower: (_make_archive_handler(aggregate), _make_restore_handler(aggregate))
    for aggregate in AGGREGATES
}
