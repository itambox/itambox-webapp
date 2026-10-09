"""Per-aggregate archive service for :class:`Kit` (#619, step 3 of the design).

A kit is an aggregate root: its :class:`~inventory.models.KitItem` rows are the
kit's definition, so deleting the kit has to move them with it instead of
dropping them. The former generic cascade was destructive here -- ``KitItem.kit``
is ``CASCADE`` and ``KitItem`` had no ``deleted_at``, so a soft-deleted kit lost
its items for good (the #619 defect table). The approved behaviour table for this
aggregate is:

* **ARCHIVE** the kit items: they are archived through their own ``save()`` with
  the operation's marker, one audited delete per row, and a restore brings back
  exactly the rows an operation archived.
* **KEEP** the journal entries: the kit's history stays as evidence and keeps
  referencing the archived kit, which stays resolvable through ``all_objects``.

Nothing references a kit from assignments -- a kit checkout produces individual
asset/inventory assignments -- so this aggregate has no REFUSE relation.

The whole operation runs in one transaction: the kit row is locked first, then
the environment is read, then the rows are written. A child failure aborts the
kit archive, so a refused or failed operation leaves no partial write.
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

from .models import Kit, KitItem


def _lock_kit(kit: Kit) -> Kit:
    """Lock the kit row under the ambient scope, or fail closed."""
    return lock_aggregate_root(kit, noun="kit")


def _live_items(kit: Kit) -> list[KitItem]:
    """The kit's live items, in a fixed (primary key) order."""
    return list(KitItem.objects.for_scope(Scope.current()).filter(kit=kit).order_by("pk"))


def _archived_items(kit: Kit) -> list[KitItem]:
    """The kit's items that an archive operation archived with it.

    A row deleted on its own (a leaf delete) carries no marker, so it is never
    resurrected by a kit restore -- the documented behaviour for rows the old
    cascade did not archive.
    """
    # unscoped: a restore has to see the rows the archive soft-deleted, which the
    # scoped default manager hides by design.
    return list(
        KitItem.all_objects.for_scope(Scope.current())
        .filter(kit=kit, deleted_at__isnull=False, archive_operation_id__isnull=False)
        .order_by("pk")
    )


def _archive_items(kit: Kit, operation: ArchiveOperation) -> int:
    """Archive the kit's live items with the operation's marker."""
    items = _live_items(kit)
    now = timezone.now()
    message = operation.archive_message()
    for item in items:
        item.deleted_at = now
        item.archive_operation_id = operation.id
        item._changelog_message = message
        item._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        item.save(update_fields=["deleted_at", "archive_operation_id"])
    return len(items)


def _restore_items(kit: Kit) -> int:
    """Restore the items that were archived with the kit."""
    items = _archived_items(kit)
    for item in items:
        item.deleted_at = None
        item.archive_operation_id = None
        item.save(update_fields=["deleted_at", "archive_operation_id"])
    return len(items)


def _kept_evidence_count(kit: Kit) -> int:
    """Journal entries that stay attached to the archived kit."""
    return kit.journal_entries.count()


def archive_kit(kit: Kit, *, actor=None, request=None) -> ArchiveResult:
    """Archive one kit with its items, or refuse with a typed :class:`ArchiveBlocked`.

    An already-archived kit is a no-op (``archived=0``): ``DELETE`` is idempotent
    for the caller, and the row lock makes a concurrent double archive safe
    instead of double-auditing.

    :param actor: the authenticated principal, for the caller's own attribution
        needs (the audit rows themselves follow the request/task context).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_kit(kit)
        if locked.deleted_at is not None:
            return ArchiveResult()
        operation = ArchiveOperation.begin(locked)

        archived_items = _archive_items(locked, operation)
        kept = _kept_evidence_count(locked)

        locked.deleted_at = timezone.now()
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(
        archived=1 + archived_items,
        kept=kept,
        operation_id=operation.id,
    )


def restore_kit(kit: Kit, *, actor=None, request=None) -> None:
    """Bring an archived kit back with its archived items, or refuse.

    The active-name unique slot is revalidated: a kit created in the meantime
    with the same name blocks the restore instead of raising an IntegrityError
    out of the request.

    :param actor: the authenticated principal (see :func:`archive_kit`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_kit(kit)
        if locked.deleted_at is None:
            return

        conflict = Kit.objects.for_scope(Scope.current()).filter(name=locked.name).exists()
        if conflict:
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: another active kit already uses the name '%(name)s'.")
                % {"object": str(locked), "name": locked.name}
            )

        _restore_items(locked)
        locked.restore()
