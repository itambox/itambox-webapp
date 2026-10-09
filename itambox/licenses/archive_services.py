"""Per-aggregate archive service for :class:`License` (#619, step 4).

A license is an aggregate root: the seats it hands out, and the kit items that
list it, both hang off it, so deleting the license has to account for them. The
approved behaviour table for this aggregate is:

* **REFUSE** while any seat is held: a live
  :class:`~licenses.models.LicenseSeatAssignment` row is a seat an operator
  handed out, and the archive never releases one on the operator's behalf. The
  refusal is a typed :class:`ArchiveBlocked` and nothing has been written when it
  is raised.
* **REFUSE** while a live :class:`~inventory.models.KitItem` lists the license:
  an active kit still needs the entitlement its line promises.
* **ARCHIVE** the released seats. A released seat is already archived -- checking
  a seat in soft-deletes its row, which is the tombstone the recycle bin shows --
  so the licence archive carries it as it is and writes nothing for it; the
  restore brings back the license only (a released seat stays released, it is
  never resurrected as a held seat behind the operator's back).
* **KEEP** the purchase order lines that name the license, and its journal
  entries and attachments: they are evidence that keeps referencing the archived
  license, which stays resolvable through ``all_objects``.

No schema change: this aggregate archives no live child, so it needs no
correlation marker.

The whole operation runs in one transaction: the license row is locked first,
then the environment is read, then the archive is written. A failed step aborts
the archive, so a refused or failed operation leaves no partial write.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db import transaction
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
from inventory.models import KitItem
from procurement.models import PurchaseOrderLine

from .models import License, LicenseSeatAssignment


def _lock_license(license_row: License) -> License:
    """Lock the license row under the ambient scope, or fail closed."""
    return lock_aggregate_root(license_row, noun="license")


def _scoped(model):
    return model.objects.for_scope(Scope.current())


def _held_seats(license_row: License) -> list[LicenseSeatAssignment]:
    """The seats currently handed out, in a fixed (primary key) order."""
    return list(_scoped(LicenseSeatAssignment).filter(license=license_row).order_by("pk"))


def _live_kit_items(license_row: License) -> list[KitItem]:
    """The live kit items that still list the license, in a fixed order."""
    return list(_scoped(KitItem).filter(license=license_row).order_by("pk"))


def _blocker_counts(license_row: License) -> list[tuple[str, int]]:
    """``(label, count)`` for every REFUSE condition that currently holds."""
    checks = [
        (_("held seats"), _scoped(LicenseSeatAssignment).filter(license=license_row).count()),
        (_("live kit items listing the license"), _scoped(KitItem).filter(license=license_row).count()),
    ]
    return [(label, count) for label, count in checks if count]


def _refusal(license_row: License, blockers: Sequence[tuple[str, int]]) -> ArchiveBlocked:
    total = sum(count for _label, count in blockers)
    detail = ", ".join(f"{count} {label}" for label, count in blockers)
    headline = ngettext(
        "Cannot delete %(object)s: %(count)s dependent record must be resolved first (%(detail)s).",
        "Cannot delete %(object)s: %(count)s dependent records must be resolved first (%(detail)s).",
        total,
    ) % {"object": str(license_row), "count": total, "detail": detail}
    return ArchiveBlocked(headline, blockers=blockers)


def _kept_evidence_count(license_row: License) -> int:
    """Rows that stay attached to the archived license.

    The lines that name it, its journal entries and attachments, plus the seat
    tombstones a previous check-in already released: those keep referencing the
    archived license and are never resurrected by a restore.
    """
    # unscoped: released seats are soft-deleted tombstones the scoped default
    # manager hides by design; they are bound to the already scoped license.
    released_seats = LicenseSeatAssignment.all_objects.filter(license=license_row, deleted_at__isnull=False).count()
    return (
        _scoped(PurchaseOrderLine).filter(license=license_row).count()
        + released_seats
        + license_row.journal_entries.count()
        + license_row.file_attachments.count()
        + license_row.image_attachments.count()
    )


def archive_license(license_row: License, *, actor=None, request=None) -> ArchiveResult:
    """Archive one license, or refuse with a typed :class:`ArchiveBlocked`.

    An already-archived license is a no-op (``archived=0``): ``DELETE`` is
    idempotent for the caller, and the row lock makes a concurrent double archive
    safe instead of double-auditing.

    :param actor: the authenticated principal, for the caller's own attribution
        needs (the audit rows themselves follow the request/task context).
    :param request: the originating request, when there is one.
    :raises ArchiveBlocked: a seat is still held, or a live kit item lists it.
    """
    with transaction.atomic():
        locked = _lock_license(license_row)
        if locked.deleted_at is not None:
            return ArchiveResult()

        blockers = _blocker_counts(locked)
        if blockers:
            raise _refusal(locked, blockers)

        operation = ArchiveOperation.begin(locked)
        kept = _kept_evidence_count(locked)

        locked.deleted_at = timezone.now()
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(archived=1, kept=kept, operation_id=operation.id)


def restore_license(license_row: License, *, actor=None, request=None) -> None:
    """Bring an archived license back, or refuse.

    The license carries no active-name slot -- two licenses may share a name --
    so no conditional unique constraint has to be revalidated. Released seats are
    not resurrected: they were released before the license was archived and stay
    released.

    :param actor: the authenticated principal (see :func:`archive_license`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_license(license_row)
        if locked.deleted_at is None:
            return
        locked.restore()
