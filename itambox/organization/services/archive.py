"""Per-aggregate archive service for :class:`AssetHolder` (pilot of #619).

Deleting an asset holder is an *archive of an aggregate root*, not a single-row
flag flip. While the person still holds something, the delete must be refused
instead of quietly orphaning the obligation — the documented consequence of the
former generic cascade: a soft-deleted holder kept an active asset assignment
(#608). This module is the pilot implementation of the per-aggregate model
designed on #619, deliberately without a schema change.

Approved contract for this aggregate (the AssetHolder tables on #619):

* **REFUSE** while any open obligation exists: active asset assignments, open
  accessory/component/consumable checkouts, held license seats, open asset
  requests, live reservations, and pending custody signing sessions. Nothing is
  released on the operator's behalf.
* **DETACH** what must not keep pointing at an archived holder: the holder's own
  ``user`` link is cleared, and the subscriptions covering the holder are ended.
  Restore never re-attaches either.
* **KEEP** evidence rows: closed assignments, custody receipts and journal
  entries are left exactly as they are.

The whole operation runs in one transaction: the holder row is locked first,
then the environment is read, then the archive is written. Every touched row
goes through its own ``save()``/service so hooks and ``ObjectChange`` entries are
written per row, all under the acting request's ``request_id``.

Event emission is deliberately unchanged for the archived root in this pilot: a
soft delete writes the audit row through ``save()`` exactly as it does today, and
the old cascade-only event signal is not extended. Unifying delete-event
semantics belongs to the ``SoftDeleteMixin`` shrinkage step of #619.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from compliance.models import CustodyReceipt
from core.archive_handlers import (
    ArchiveBlocked,
    ArchiveOperation,
    ArchiveResult,
    lock_aggregate_root,
)
from core.choices import ObjectChangeActionChoices
from core.managers import Scope
from subscriptions.models import SubscriptionAssignment

from ..models import AssetHolder
from .offboarding import ObligationItem, get_offboarding_report

#: Obligation kinds that make an asset holder non-archivable — the REFUSE column
#: of the #619 table. Every one of them is an open, unreturned item, and the
#: archive never releases one on the operator's behalf. ``custody_receipt``,
#: ``subscription`` and ``membership`` are deliberately absent: receipts stay as
#: evidence, subscription assignments are detached below, and a membership
#: belongs to the user rather than to the holder.
BLOCKING_OBLIGATION_KINDS = frozenset(
    {
        "asset_assignment",
        "accessory_assignment",
        "component_allocation",
        "consumable_assignment",
        "license_seat",
        "asset_request",
        "asset_reservation",
        "custody_signing_session",
    }
)


def _lock_holder(holder: AssetHolder) -> AssetHolder:
    """Lock the holder row under the ambient scope, or fail closed."""
    return lock_aggregate_root(holder, noun="asset holder")


def _blocking_obligations(holder: AssetHolder) -> list[ObligationItem]:
    """Open obligations that refuse the archive, from the offboarding report.

    The report is the single composition of "what does this person still owe",
    so the archive reuses it rather than maintaining a weaker duplicate list; the
    kinds outside the REFUSE column are filtered out here.
    """
    report = get_offboarding_report(holder)
    return [item for item in report.items if item.kind in BLOCKING_OBLIGATION_KINDS]


def _refusal(holder: AssetHolder, blockers: Sequence[ObligationItem]) -> ArchiveBlocked:
    count = len(blockers)
    headline = ngettext(
        "Cannot delete %(object)s: %(count)s open obligation must be resolved first.",
        "Cannot delete %(object)s: %(count)s open obligations must be resolved first.",
        count,
    ) % {"object": str(holder), "count": count}
    return ArchiveBlocked(headline, blockers=blockers)


def _detach_user_link(holder: AssetHolder) -> int:
    """Clear the holder's login link so the conditional unique slot is free."""
    if holder.user_id is None:
        return 0
    holder.user = None
    return 1


def _end_subscription_assignments(holder: AssetHolder, operation: ArchiveOperation) -> int:
    """End the subscriptions covering this holder.

    A subscription assignment is a leaf row of its subscription and carries its
    own ``deleted_at`` since #619 step 3, so ending it is a leaf soft delete
    through the row's own ``delete()``: one audited delete per assignment, and no
    live row keeps pointing at the archived holder. The row keeps no
    ``archive_operation_id``, so a later subscription restore never resurrects an
    assignment that was ended here, and the conditional
    ``(subscription, content_type, object_id)`` unique slot is free for a
    re-assignment.
    """
    holder_ct = ContentType.objects.get_for_model(AssetHolder)
    assignments = SubscriptionAssignment.objects.for_scope(Scope.current()).filter(
        content_type=holder_ct,
        object_id=holder.pk,
    )
    ended = 0
    for assignment in assignments:
        assignment._changelog_message = operation.detach_message()
        assignment.delete()
        ended += 1
    return ended


def _kept_evidence_count(holder: AssetHolder) -> int:
    """Custody receipts that stay attached to the archived holder.

    ``CustodyReceipt`` keeps an unscoped default manager (its public bearer-token
    sign route must resolve a receipt regardless of tenant context), so it is
    scoped on ``asset__tenant`` exactly as the offboarding report does.
    """
    return CustodyReceipt.objects.filter(holder=holder, asset__tenant=holder.tenant).count()


def archive_holder(holder: AssetHolder, *, actor=None, request=None) -> ArchiveResult:
    """Archive one asset holder, or refuse with a typed :class:`ArchiveBlocked`.

    An already-archived holder is a no-op (``archived=0``): ``DELETE`` is
    idempotent for the caller, and the row lock makes a concurrent double archive
    safe instead of double-auditing.

    :param actor: the authenticated principal, for the caller's own attribution
        needs (the audit rows themselves follow the request/task context).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_holder(holder)
        if locked.deleted_at is not None:
            return ArchiveResult()
        operation = ArchiveOperation.begin(locked)

        blockers = _blocking_obligations(locked)
        if blockers:
            raise _refusal(locked, blockers)

        user_link_detached = _detach_user_link(locked)
        update_fields = ["deleted_at", "user"] if user_link_detached else ["deleted_at"]
        detached = user_link_detached + _end_subscription_assignments(locked, operation)
        kept = _kept_evidence_count(locked)

        locked.deleted_at = timezone.now()
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=update_fields)

    return ArchiveResult(archived=1, detached=detached, kept=kept, operation_id=operation.id)


def restore_holder(holder: AssetHolder, *, actor=None, request=None) -> None:
    """Bring an archived holder back, or refuse with a typed :class:`ArchiveBlocked`.

    The user link is not restored (it was detached by the archive), so only the
    ``(tenant, upn)`` unique slot can collide: a holder created in the meantime
    with the same principal name blocks the restore instead of raising an
    IntegrityError out of the request.

    :param actor: the authenticated principal (see :func:`archive_holder`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_holder(holder)
        if locked.deleted_at is None:
            return

        conflict = (
            AssetHolder.objects.for_scope(Scope.current()).filter(tenant_id=locked.tenant_id, upn=locked.upn).exists()
        )
        if conflict:
            raise ArchiveBlocked(
                _(
                    "Cannot restore %(object)s: another active asset holder already uses the user principal "
                    "name '%(upn)s'."
                )
                % {"object": str(locked), "upn": locked.upn}
            )

        locked.restore()
