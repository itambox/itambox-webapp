"""Per-aggregate archive service for :class:`Asset` (#619, step 4b).

An asset is the largest aggregate root: assignments, maintenance, reservations,
warranties, installed software, license seats and inventory checkouts all hang
off it. The approved behaviour table for this aggregate (design section 2.2) is:

* **REFUSE** while the asset is still in somebody's hands or in progress: an
  active assignment (as the asset or as the assigned target), an open
  maintenance, a live reservation, a held license seat, or an open accessory,
  component or consumable checkout targeting the asset. The refusal is a typed
  :class:`ArchiveBlocked` and nothing has been written when it is raised.
* **DETACH** what must not keep pointing at an archived asset: open asset
  requests are cancelled, the subscriptions covering the asset are ended and the
  asset is removed from the contracts that cover it. Restore never re-attaches
  any of these.
* **ARCHIVE** the asset's own history through each row's ``save()``: closed
  assignments, finished maintenance, warranties, non-live reservations and the
  installed software, all stamped with the operation marker so a restore brings
  back exactly these rows and nothing that was deleted on its own.
* **KEEP** evidence: disposals, custody receipts, audit rows, closed requests,
  journal entries and attachments keep referencing the archived asset.

The whole operation runs in one transaction: the asset row is locked first, then
the environment is read, then the archive is written. A failed step aborts the
archive, so a refused or failed operation leaves no partial write.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.archive_handlers import (
    ArchiveBlocked,
    ArchiveOperation,
    ArchiveResult,
    lock_aggregate_root,
)
from core.choices import ObjectChangeActionChoices
from core.managers import Scope

from .choices import RequestStatusChoices
from .models import (
    Asset,
    AssetAssignment,
    AssetMaintenance,
    AssetRequest,
    AssetReservation,
    Warranty,
)
from .models.choices import MaintenanceStatusChoices, ReservationStatusChoices

_OPEN_REQUEST_STATUSES = (
    RequestStatusChoices.PENDING,
    RequestStatusChoices.APPROVED,
    RequestStatusChoices.PROCUREMENT,
)
_OPEN_MAINTENANCE_STATUSES = (MaintenanceStatusChoices.SCHEDULED, MaintenanceStatusChoices.IN_PROGRESS)
_LIVE_RESERVATION_STATUSES = (ReservationStatusChoices.PENDING, ReservationStatusChoices.ACTIVE)
_INVENTORY_ASSIGNMENTS = ("AccessoryAssignment", "ComponentAllocation", "ConsumableAssignment")


def _scoped(model) -> QuerySet:
    return model.objects.for_scope(Scope.current())


def _including_deleted(model) -> QuerySet:
    # unscoped: archived rows are hidden from the default manager by design;
    # the ambient scope is applied explicitly where the manager supports it.
    manager = model.all_objects
    if hasattr(manager, "for_scope"):
        return manager.for_scope(Scope.current())
    # rows keyed to the asset (license seats) carry no scoped manager; every
    # caller already binds the query to the locked asset.
    return manager.all()


def _model(app_label: str, name: str):
    # Resolved through the app registry: these apps import ``assets`` models, so a
    # static import here would close an import cycle.
    return apps.get_model(app_label, name)


def _installed_software():
    return _model("software", "InstalledSoftware")


def _seat_model():
    return _model("licenses", "LicenseSeatAssignment")


def _inventory_assignment_models() -> list:
    return [_model("inventory", name) for name in _INVENTORY_ASSIGNMENTS]


def _active_assignments(asset: Asset) -> QuerySet:
    return _scoped(AssetAssignment).filter(Q(asset=asset) | Q(assigned_asset=asset), is_active=True)


def _blocker_counts(asset: Asset) -> list[tuple[str, int]]:
    """``(label, count)`` for every REFUSE condition that currently holds."""
    checkouts = sum(_scoped(model).filter(assigned_asset=asset).count() for model in _inventory_assignment_models())
    checks = [
        (_("active assignments"), _active_assignments(asset).count()),
        (
            _("open maintenance records"),
            _scoped(AssetMaintenance).filter(asset=asset, status__in=_OPEN_MAINTENANCE_STATUSES).count(),
        ),
        (
            _("live reservations"),
            _scoped(AssetReservation).filter(asset=asset, status__in=_LIVE_RESERVATION_STATUSES).count(),
        ),
        (_("held license seats"), _scoped(_seat_model()).filter(asset=asset).count()),
        (_("open inventory checkouts"), checkouts),
    ]
    return [(label, count) for label, count in checks if count]


def _refusal(asset: Asset, blockers: Sequence[tuple[str, int]]) -> ArchiveBlocked:
    total = sum(count for _label, count in blockers)
    detail = ", ".join(f"{count} {label}" for label, count in blockers)
    headline = ngettext(
        "Cannot delete %(object)s: %(count)s dependent record must be resolved first (%(detail)s).",
        "Cannot delete %(object)s: %(count)s dependent records must be resolved first (%(detail)s).",
        total,
    ) % {"object": str(asset), "count": total, "detail": detail}
    return ArchiveBlocked(headline, blockers=blockers)


def _archivable_rows(asset: Asset) -> list[QuerySet]:
    """The leaf rows an archive moves, after the REFUSE checks have passed."""
    return [
        _scoped(AssetAssignment).filter(asset=asset, is_active=False),
        _scoped(AssetMaintenance).exclude(status__in=_OPEN_MAINTENANCE_STATUSES).filter(asset=asset),
        _scoped(Warranty).filter(asset=asset),
        _scoped(AssetReservation).filter(asset=asset).exclude(status__in=_LIVE_RESERVATION_STATUSES),
        _scoped(_installed_software()).filter(asset=asset),
    ]


def _archive_rows(querysets: Sequence[QuerySet], when, operation: ArchiveOperation) -> int:
    """Archive the rows with the operation's marker, one audit entry each."""
    message = operation.archive_message()
    archived = 0
    for queryset in querysets:
        for row in queryset.select_for_update().order_by("pk"):
            row.deleted_at = when
            row.archive_operation_id = operation.id
            row._changelog_message = message
            row._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
            row.save(update_fields=["deleted_at", "archive_operation_id"])
            archived += 1
    return archived


def _detach_open_requests(asset: Asset, operation: ArchiveOperation) -> int:
    """Cancel the open asset requests that name the asset, one audit entry each.

    A request keeps its asset link (it names exactly one), so detaching it from
    the archived asset means closing it.
    """
    requests = _scoped(AssetRequest).filter(Q(asset=asset) | Q(assigned_asset=asset), status__in=_OPEN_REQUEST_STATUSES)
    detached = 0
    for request in requests.select_for_update().order_by("pk"):
        request.status = RequestStatusChoices.CANCELLED
        request._changelog_message = operation.detach_message()
        request.save(update_fields=["status"])
        detached += 1
    return detached


def _end_subscription_assignments(asset: Asset, operation: ArchiveOperation) -> int:
    """End the subscriptions covering the asset, one audited delete each."""
    assignment_model = _model("subscriptions", "SubscriptionAssignment")
    content_type = ContentType.objects.get_for_model(Asset)
    ended = 0
    for assignment in _scoped(assignment_model).filter(content_type=content_type, object_id=asset.pk):
        assignment._changelog_message = operation.detach_message()
        assignment.delete()
        ended += 1
    return ended


def _detach_contracts(asset: Asset) -> int:
    """Remove the asset from the contracts that cover it (the M2M rows only)."""
    through = _model("procurement", "Contract").assets.through
    rows = through.objects.filter(asset_id=asset.pk)
    count = rows.count()
    rows.delete()
    return count


def _kept_evidence_count(asset: Asset) -> int:
    """Evidence rows that stay attached to the archived asset."""
    closed_requests = (
        _scoped(AssetRequest)
        .filter(Q(asset=asset) | Q(assigned_asset=asset))
        .exclude(status__in=_OPEN_REQUEST_STATUSES)
        .count()
    )
    closed_target_assignments = _scoped(AssetAssignment).filter(assigned_asset=asset, is_active=False).count()
    released_seats = _including_deleted(_seat_model()).filter(asset=asset, deleted_at__isnull=False).count()
    disposals = asset.disposals.count()
    # unscoped: evidence counts must include soft-deleted rows; the asset is
    # already locked and scope-checked by the caller.
    receipts = _model("compliance", "CustodyReceipt")._base_manager.filter(asset=asset).count()
    # unscoped: same reason as the receipt count above.
    audits = _model("compliance", "AssetAudit")._base_manager.filter(asset=asset).count()
    return (
        closed_requests
        + closed_target_assignments
        + released_seats
        + disposals
        + receipts
        + audits
        + asset.journal_entries.count()
        + asset.file_attachments.count()
        + asset.image_attachments.count()
    )


def archive_asset(asset: Asset, *, actor=None, request=None) -> ArchiveResult:
    """Archive one asset with its history, or refuse with a typed :class:`ArchiveBlocked`.

    An already-archived asset is a no-op (``archived=0``): ``DELETE`` is
    idempotent for the caller, and the row lock makes a concurrent double archive
    safe instead of double-auditing.

    :param actor: the authenticated principal, for the caller's own attribution
        needs (the audit rows themselves follow the request/task context).
    :param request: the originating request, when there is one.
    :raises ArchiveBlocked: the asset is still assigned, in maintenance,
        reserved, holds a license seat or is checked out of inventory.
    """
    with transaction.atomic():
        locked = lock_aggregate_root(asset, noun="asset")
        if locked.deleted_at is not None:
            return ArchiveResult()

        blockers = _blocker_counts(locked)
        if blockers:
            raise _refusal(locked, blockers)

        operation = ArchiveOperation.begin(locked)
        when = timezone.now()
        archived_rows = _archive_rows(_archivable_rows(locked), when, operation)
        detached = (
            _detach_open_requests(locked, operation)
            + _end_subscription_assignments(locked, operation)
            + _detach_contracts(locked)
        )
        kept = _kept_evidence_count(locked)

        locked.deleted_at = when
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(archived=1 + archived_rows, detached=detached, kept=kept, operation_id=operation.id)


def _archived_rows(asset: Asset) -> list:
    """The leaf rows this asset's archive moved (same instant, with a marker).

    A row deleted on its own, or by another operation, carries a different
    ``deleted_at`` and is never resurrected here.
    """
    models = (AssetAssignment, AssetMaintenance, Warranty, AssetReservation, _installed_software())
    rows: list = []
    for model in models:
        rows.extend(
            _including_deleted(model)
            .select_for_update()
            .filter(asset=asset, deleted_at=asset.deleted_at, archive_operation_id__isnull=False)
            .order_by("pk")
        )
    return rows


def _assert_restore_slots_free(asset: Asset, rows: Sequence) -> None:
    """Refuse a restore that would collide with an active unique slot."""
    if (
        asset.serial_number
        and _scoped(Asset)
        .filter(tenant_id=asset.tenant_id, serial_number=asset.serial_number)
        .exclude(pk=asset.pk)
        .exists()
    ):
        raise ArchiveBlocked(
            _("Cannot restore %(object)s: another active asset already uses the serial number '%(serial)s'.")
            % {"object": str(asset), "serial": asset.serial_number}
        )
    installed = _installed_software()
    for row in rows:
        if not isinstance(row, installed):
            continue
        if (
            _scoped(installed)
            .filter(asset=asset, software_id=row.software_id, version_detected=row.version_detected)
            .exists()
        ):
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: an installed software entry was recorded again after the archive.")
                % {"object": str(asset)}
            )


def restore_asset(asset: Asset, *, actor=None, request=None) -> None:
    """Bring an archived asset back with the history its archive moved, or refuse.

    The active serial-number slot and the active installed-software slot are
    revalidated and reported as a typed refusal instead of an IntegrityError.
    Detached requests, subscriptions and contract links are not re-attached.

    :param actor: the authenticated principal (see :func:`archive_asset`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = lock_aggregate_root(asset, noun="asset")
        if locked.deleted_at is None:
            return

        rows = _archived_rows(locked)
        _assert_restore_slots_free(locked, rows)

        for row in rows:
            row.deleted_at = None
            row.archive_operation_id = None
            row.save(update_fields=["deleted_at", "archive_operation_id"])
        locked.restore()
