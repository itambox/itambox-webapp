"""Per-aggregate archive service for :class:`Location` (#619, step 3c).

The approved behaviour table for this aggregate:

* **REFUSE** while anything still depends on the location: live assets, active
  asset assignments, live inventory checkouts/allocations, stock rows with a
  positive quantity, live child locations and open purchase orders. The refusal
  is a typed :class:`ArchiveBlocked` and nothing has been written when it is
  raised.
* **ARCHIVE** the empty stock rows through their own ``save()`` (leaf
  ``deleted_at`` plus the operation marker); a restore brings back exactly the
  rows this operation archived.
* **DETACH** open asset requests and open audit sessions (the location link is
  cleared with one audited update each) and end the subscriptions covering the
  location. Restore never re-attaches them.
* **KEEP** closed requests/assignments, finished audits, audit results and
  journal entries.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetAssignment, AssetRequest
from compliance.choices import AuditSessionStatusChoices
from compliance.models import AuditSession
from core.archive_handlers import ArchiveBlocked, ArchiveOperation, ArchiveResult, lock_aggregate_root
from core.choices import ObjectChangeActionChoices
from core.managers import Scope
from inventory.models import (
    AccessoryAssignment,
    AccessoryStock,
    ComponentAllocation,
    ComponentStock,
    ConsumableAssignment,
    ConsumableStock,
)
from procurement.models import PurchaseOrder
from subscriptions.models import SubscriptionAssignment

from ..models import Location

_OPEN_REQUEST_STATUSES = (
    RequestStatusChoices.PENDING,
    RequestStatusChoices.APPROVED,
    RequestStatusChoices.PROCUREMENT,
)
_OPEN_AUDIT_STATUSES = (AuditSessionStatusChoices.PLANNED, AuditSessionStatusChoices.ACTIVE)
_CLOSED_PO_STATUSES = (PurchaseOrder.STATUS_RECEIVED, PurchaseOrder.STATUS_CANCELLED)
_STOCK_MODELS = (ComponentStock, AccessoryStock, ConsumableStock)
_ASSIGNMENT_MODELS = (ComponentAllocation, AccessoryAssignment, ConsumableAssignment)


def _lock_location(location: Location) -> Location:
    return lock_aggregate_root(location, noun="location")


def _scoped(model):
    return model.objects.for_scope(Scope.current())


def _blocker_counts(location: Location) -> list[tuple[str, int]]:
    """``(label, count)`` for every REFUSE condition that currently holds."""
    both = Q(assigned_location=location) | Q(from_location=location)
    active_assignments = _scoped(AssetAssignment).filter(assigned_location=location, is_active=True)
    open_orders = _scoped(PurchaseOrder).filter(destination_location=location).exclude(status__in=_CLOSED_PO_STATUSES)
    checks = [
        (_("assets"), _scoped(Asset).filter(location=location).count()),
        (_("active asset assignments"), active_assignments.count()),
        (_("child locations"), _scoped(Location).filter(parent=location).count()),
        (_("open purchase orders"), open_orders.count()),
        (
            _("stock rows with remaining quantity"),
            sum(_scoped(model).filter(location=location, qty__gt=0).count() for model in _STOCK_MODELS),
        ),
        (_("open inventory checkouts"), sum(_scoped(model).filter(both).count() for model in _ASSIGNMENT_MODELS)),
    ]
    return [(label, count) for label, count in checks if count]


def _refusal(location: Location, blockers: Sequence[tuple[str, int]]) -> ArchiveBlocked:
    total = sum(count for _label, count in blockers)
    detail = ", ".join(f"{count} {label}" for label, count in blockers)
    headline = ngettext(
        "Cannot delete %(object)s: %(count)s dependent record must be resolved first (%(detail)s).",
        "Cannot delete %(object)s: %(count)s dependent records must be resolved first (%(detail)s).",
        total,
    ) % {"object": str(location), "count": total, "detail": detail}
    return ArchiveBlocked(headline, blockers=blockers)


def _lock_empty_stock(location: Location) -> list:
    rows: list = []
    for model in _STOCK_MODELS:
        rows.extend(_scoped(model).select_for_update().filter(location=location).order_by("pk"))
    return rows


def _archive_stock(rows: list, when, operation: ArchiveOperation) -> int:
    message = operation.archive_message()
    for row in rows:
        row.deleted_at = when
        row.archive_operation_id = operation.id
        row._changelog_message = message
        row._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        row.save(update_fields=["deleted_at", "archive_operation_id"])
    return len(rows)


def _detach_field(queryset, fields: Sequence[str], location: Location, operation: ArchiveOperation) -> int:
    detached = 0
    for obj in queryset.select_for_update().order_by("pk"):
        changed = [name for name in fields if getattr(obj, f"{name}_id") == location.pk]
        for name in changed:
            setattr(obj, name, None)
        obj._changelog_message = operation.detach_message()
        obj.save(update_fields=changed)
        detached += 1
    return detached


def _detach_open_records(location: Location, operation: ArchiveOperation) -> int:
    requests = (
        _scoped(AssetRequest)
        .filter(status__in=_OPEN_REQUEST_STATUSES)
        .filter(Q(source_location=location) | Q(assigned_location=location))
    )
    sessions = _scoped(AuditSession).filter(location=location, status__in=_OPEN_AUDIT_STATUSES)
    return _detach_field(requests, ("source_location", "assigned_location"), location, operation) + _detach_field(
        sessions, ("location",), location, operation
    )


def _end_subscription_assignments(location: Location, operation: ArchiveOperation) -> int:
    assignments = _scoped(SubscriptionAssignment).filter(
        content_type=ContentType.objects.get_for_model(Location), object_id=location.pk
    )
    ended = 0
    for assignment in assignments:
        assignment._changelog_message = operation.detach_message()
        assignment.delete()
        ended += 1
    return ended


def _kept_evidence_count(location: Location) -> int:
    closed_requests = (
        _scoped(AssetRequest)
        .exclude(status__in=_OPEN_REQUEST_STATUSES)
        .filter(Q(source_location=location) | Q(assigned_location=location))
        .count()
    )
    return closed_requests + _scoped(AssetAssignment).filter(assigned_location=location, is_active=False).count()


def archive_location(location: Location, *, actor=None, request=None) -> ArchiveResult:
    """Archive one location with its empty stock rows, or refuse.

    An already-archived location is a no-op (``archived=0``).

    :raises ArchiveBlocked: something still depends on the location.
    """
    with transaction.atomic():
        locked = _lock_location(location)
        if locked.deleted_at is not None:
            return ArchiveResult()
        blockers = _blocker_counts(locked)
        if blockers:
            raise _refusal(locked, blockers)

        operation = ArchiveOperation.begin(locked)
        when = timezone.now()
        archived_stock = _archive_stock(_lock_empty_stock(locked), when, operation)
        detached = _detach_open_records(locked, operation) + _end_subscription_assignments(locked, operation)
        kept = _kept_evidence_count(locked)

        locked.deleted_at = when
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])
    return ArchiveResult(archived=1 + archived_stock, detached=detached, kept=kept, operation_id=operation.id)


def restore_location(location: Location, *, actor=None, request=None) -> None:
    """Bring an archived location back with the stock rows its archive moved."""
    with transaction.atomic():
        locked = _lock_location(location)
        if locked.deleted_at is None:
            return
        if locked.parent_id and not _scoped(Location).filter(pk=locked.parent_id).exists():
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: its parent location is archived or unavailable.")
                % {"object": str(locked)}
            )
        for model in _STOCK_MODELS:
            tombstones = model.all_objects  # unscoped: restore must see tombstoned rows; narrowed by for_scope below
            rows = (
                tombstones.for_scope(Scope.current())
                .select_for_update()
                .filter(location=locked, deleted_at=locked.deleted_at, archive_operation_id__isnull=False)
                .order_by("pk")
            )
            for row in rows:
                row.deleted_at = None
                row.archive_operation_id = None
                row.save(update_fields=["deleted_at", "archive_operation_id"])
        locked.restore()
