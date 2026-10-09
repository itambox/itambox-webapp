"""Per-aggregate archive service for :class:`Subscription` (#619, step 3).

A subscription is an aggregate root: the assignments that say who it covers, and
the licenses it funds, both hang off it. Deleting the subscription therefore has
to move them with it. The approved behaviour table for this aggregate is:

* **ARCHIVE** the :class:`~subscriptions.models.SubscriptionAssignment` rows: they
  are a leaf relation of the subscription and carry their own ``deleted_at``, so
  they are archived through their own ``save()`` with the operation's marker, one
  audited delete per row, and a restore brings exactly the rows an operation
  archived back.
* **DETACH** the licenses that name this subscription as their funding
  agreement: each is unlinked through its own ``save()`` with one audited update
  per license, so no live license keeps pointing at an archived subscription.
  Restore never re-attaches them (the operator re-links them deliberately).
* **KEEP** the journal entries and the file/image attachments: they are evidence
  and keep referencing the archived subscription, which stays resolvable through
  ``all_objects``.

The aggregate has no REFUSE relation: nothing about a subscription has to be
resolved before it can be archived.

The whole operation runs in one transaction: the subscription row is locked
first, then the environment is read, then the rows are written. A child failure
aborts the archive, so a refused or failed operation leaves no partial write.
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
from licenses.models import License

from .models import Subscription, SubscriptionAssignment


def _lock_subscription(subscription: Subscription) -> Subscription:
    """Lock the subscription row under the ambient scope, or fail closed."""
    return lock_aggregate_root(subscription, noun="subscription")


def _live_assignments(subscription: Subscription) -> list[SubscriptionAssignment]:
    """The subscription's live assignments, in a fixed (primary key) order."""
    return list(
        SubscriptionAssignment.objects.for_scope(Scope.current()).filter(subscription=subscription).order_by("pk")
    )


def _archived_assignments(subscription: Subscription) -> list[SubscriptionAssignment]:
    """The assignments an archive operation archived with this subscription.

    A row ended on its own (a holder detachment, or a leaf delete) carries no
    marker, so a subscription restore never resurrects it -- the documented
    behaviour for rows the old cascade did not archive.
    """
    # unscoped: a restore has to see the rows the archive soft-deleted, which the
    # scoped default manager hides by design.
    return list(
        SubscriptionAssignment.all_objects.for_scope(Scope.current())
        .filter(subscription=subscription, deleted_at__isnull=False, archive_operation_id__isnull=False)
        .order_by("pk")
    )


def _archive_assignments(subscription: Subscription, operation: ArchiveOperation) -> int:
    """Archive the subscription's live assignments with the operation's marker."""
    assignments = _live_assignments(subscription)
    now = timezone.now()
    message = operation.archive_message()
    for assignment in assignments:
        assignment.deleted_at = now
        assignment.archive_operation_id = operation.id
        assignment._changelog_message = message
        assignment._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        assignment.save(update_fields=["deleted_at", "archive_operation_id"])
    return len(assignments)


def _detach_licenses(subscription: Subscription, operation: ArchiveOperation) -> int:
    """Unlink the licenses funded by this subscription, one audit entry each."""
    licenses = list(License.objects.for_scope(Scope.current()).filter(subscription=subscription).order_by("pk"))
    for license_row in licenses:
        license_row.subscription = None
        license_row._changelog_message = operation.detach_message()
        license_row.save(update_fields=["subscription"])
    return len(licenses)


def _kept_evidence_count(subscription: Subscription) -> int:
    """Journal entries and attachments that stay attached to the subscription."""
    return (
        subscription.journal_entries.count()
        + subscription.file_attachments.count()
        + subscription.image_attachments.count()
    )


def _restore_assignments(assignments: list[SubscriptionAssignment]) -> None:
    """Bring the archived assignments back and release the operation marker."""
    for assignment in assignments:
        assignment.deleted_at = None
        assignment.archive_operation_id = None
        assignment.save(update_fields=["deleted_at", "archive_operation_id"])


def _assert_restore_targets_free(subscription: Subscription, assignments: list[SubscriptionAssignment]) -> None:
    """Refuse the restore while a target already carries a live assignment again.

    ``subscriptions_assignment_unique`` is a conditional unique constraint (active
    rows only), so an assignment re-created for the same target after the archive
    does not collide with the archived row -- until the restore would make two
    live rows for one target. Revalidate that here and report it instead of
    raising an IntegrityError out of the request.
    """
    live = SubscriptionAssignment.objects.for_scope(Scope.current()).filter(subscription=subscription)
    for assignment in assignments:
        if live.filter(content_type_id=assignment.content_type_id, object_id=assignment.object_id).exists():
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: one of its targets is already covered by a live subscription assignment.")
                % {"object": str(subscription)}
            )


def archive_subscription(subscription: Subscription, *, actor=None, request=None) -> ArchiveResult:
    """Archive one subscription with its assignments, or refuse.

    An already-archived subscription is a no-op (``archived=0``): ``DELETE`` is
    idempotent for the caller, and the row lock makes a concurrent double archive
    safe instead of double-auditing.

    :param actor: the authenticated principal, for the caller's own attribution
        needs (the audit rows themselves follow the request/task context).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_subscription(subscription)
        if locked.deleted_at is not None:
            return ArchiveResult()
        operation = ArchiveOperation.begin(locked)

        detached = _detach_licenses(locked, operation)
        archived_assignments = _archive_assignments(locked, operation)
        kept = _kept_evidence_count(locked)

        locked.deleted_at = timezone.now()
        locked._changelog_action = ObjectChangeActionChoices.ACTION_DELETE
        locked.save(update_fields=["deleted_at"])

    return ArchiveResult(
        archived=1 + archived_assignments,
        detached=detached,
        kept=kept,
        operation_id=operation.id,
    )


def restore_subscription(subscription: Subscription, *, actor=None, request=None) -> None:
    """Bring an archived subscription back with its archived assignments, or refuse.

    Two conditional slots are revalidated: the active-slug unique constraint (a
    subscription created in the meantime with the same slug) and the active
    assignment target constraint (a target that is covered again). Detached
    licenses are not re-attached.

    :param actor: the authenticated principal (see :func:`archive_subscription`).
    :param request: the originating request, when there is one.
    """
    with transaction.atomic():
        locked = _lock_subscription(subscription)
        if locked.deleted_at is None:
            return

        conflict = Subscription.objects.for_scope(Scope.current()).filter(slug=locked.slug).exists()
        if conflict:
            raise ArchiveBlocked(
                _("Cannot restore %(object)s: another active subscription already uses the slug '%(slug)s'.")
                % {"object": str(locked), "slug": locked.slug}
            )

        assignments = _archived_assignments(locked)
        _assert_restore_targets_free(locked, assignments)

        _restore_assignments(assignments)
        locked.restore()
