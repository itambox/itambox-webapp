"""Seed self-check for operational-story coherence (#506).

The demo dataset is a sales and QA artefact, so the stories it tells must obey the
product's own lifecycle rules. Four seed phases used to contradict the product they
demonstrate:

1. ``_seed_procurement`` materialised at most three assets per received PO line, so
   a line received with qty 10 reported 10 units while the ledger held 3.
2. ``_simulate_history`` moved actively-assigned assets into ``pending-repair`` and
   back to ``available`` without touching the assignment, so history said a device
   was in the workshop while its holder never lost it.
3. ``_seed_maintenance`` created repair records on random assets with no relation to
   their status timeline, and left ~30 % without a completion date. Out-of-service
   work is now derived from the repair windows the history simulation produced.
4. ``_seed_operations`` marked requests ``approved`` without allocating an asset, so
   the claim view dead-ended on "No asset has been allocated to this request."

``check_seed_operational_invariants()`` re-reads the seeded rows and fails closed
when any of those stories regress, mirroring the #308 self-check pattern
(``check_seed_access_invariants`` / ``check_seed_inventory_invariants``). It is
read-only and runs at the end of ``_seed_all``.

Note on the change-log reader: ``core.serialization.serialize_object`` stores
relations as bare primary keys, so an asset's recorded ``status`` is a
``StatusLabel`` pk, not a nested object. The repair-window reader below resolves
those pks against the label table.
"""

import datetime

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import CommandError
from django.db.models import Count, F, Q
from django.utils import timezone

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetAssignment, AssetMaintenance, AssetRequest, StatusLabel
from assets.models.choices import MaintenanceStatusChoices
from core.models import ObjectChange
from procurement.models import PurchaseOrderLine

#: Maintenance types that describe work the product models as taking a unit out of
#: service. Such a record is the paperwork of an episode and must belong to one.
OUT_OF_SERVICE_TYPES = ("repair", "calibration")


def _check_assignment_intervals():
    """No assignment may be impossible as a real loan of that asset.

    Two rules, both about time the product cannot have produced:

    * an assignment may not be checked back in before it was checked out. Seeded
      assignments are created in their final state, so ``checked_out_at`` defaults to
      the seed run's timestamp; back-dating only ``checked_in_at`` — exactly what a
      historical check-in does — leaves an interval that runs backwards, and such an
      assignment looks open across every window between its two stamps.
    * an assignment may not start before the asset existed. A prior-loan row dated
      relative to "now" rather than to the purchase date makes a laptop bought six
      months ago look like it was on loan 700 days ago, which then reads as "still
      held" during any repair window drawn after the purchase date.
    """
    inverted = (
        AssetAssignment._base_manager.filter(checked_in_at__isnull=False)
        .filter(Q(checked_out_at__isnull=True) | Q(checked_out_at__gt=F("checked_in_at")))
        .order_by("pk")
        .first()
    )
    if inverted is not None:
        raise CommandError(
            "Seed operational invariant failed: assignment "
            f"{inverted.pk} on asset {inverted.asset_id} was checked out at "
            f"{inverted.checked_out_at} but checked in earlier, at "
            f"{inverted.checked_in_at}."
        )

    impossible = (
        AssetAssignment._base_manager.filter(
            asset__purchase_date__isnull=False,
            checked_out_at__date__lt=F("asset__purchase_date"),
        )
        .order_by("pk")
        .first()
    )
    if impossible is not None:
        raise CommandError(
            "Seed operational invariant failed: assignment "
            f"{impossible.pk} checks out asset {impossible.asset_id} on "
            f"{impossible.checked_out_at.date()}, before the asset was bought on "
            f"{impossible.asset.purchase_date}."
        )

    future = (
        AssetAssignment._base_manager.filter(
            is_active=False,
            checked_in_at__gt=timezone.now(),
        )
        .order_by("pk")
        .first()
    )
    if future is not None:
        raise CommandError(
            "Seed operational invariant failed: assignment "
            f"{future.pk} is closed but was not checked back in until "
            f"{future.checked_in_at}, which is in the future. Its interval covers "
            "history that has not happened yet, so it reads as an open loan."
        )


def check_seed_operational_invariants():
    """Fail closed when a seeded operational story contradicts the product's rules.

    Called at the end of ``_seed_all`` so a regression in any of the four stories
    aborts the seed instead of shipping a dataset that misrepresents the product.
    """
    _check_po_receipts_materialised()
    _check_assignment_intervals()
    _check_repair_history_matches_assignments()
    _check_maintenance_matches_timeline()
    _check_approved_requests_allocated()


def _check_po_receipts_materialised():
    """A received asset-type PO line must have exactly as many assets as it reports.

    Equality, not "at least": the acceptance criterion is that ``qty_received``
    equals the number of instantiated assets, so eleven assets against ten received
    units is as much a mismatch as three — the order and the ledger must agree on
    how many units were bought. Only lines that name an ``asset_type`` are in scope:
    an accessory or consumable line is received into stock, not into the asset
    ledger, and the seed does not create Assets for it at all.
    """
    mismatched = (
        PurchaseOrderLine._base_manager.filter(qty_received__gt=0, asset_type__isnull=False)
        .annotate(materialized=Count("assets", filter=Q(assets__deleted_at__isnull=True)))
        .filter(~Q(materialized=F("qty_received")))
        .order_by("pk")
        .first()
    )
    if mismatched is not None:
        raise CommandError(
            "Seed operational invariant failed: purchase order line "
            f"{mismatched.pk} reports {mismatched.qty_received} received but only "
            f"{mismatched.materialized} asset(s) exist for it."
        )


def _repair_label_pks():
    """Primary keys of the status labels that mean "this unit is out of service"."""
    return set(
        StatusLabel._base_manager.filter(
            type__in=["pending", "in_repair"],
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
    )


def _recorded_status_pk(postchange_data):
    """Return the StatusLabel pk recorded in a change-log entry's postchange data.

    ``core.serialization.serialize_object`` stores relations as bare primary keys,
    so the recorded ``status`` is a ``StatusLabel`` pk rather than a nested object.
    """
    data = postchange_data or {}
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    return status if isinstance(status, int) else None


def _is_held(asset):
    """True when the unit is actively assigned to a person right now."""
    return asset.assignments.filter(is_active=True, assigned_user__isnull=False).exists()


def _repair_windows(repair_pks):
    """Return the repair windows recorded in the change log.

    Walks each asset's timeline: a transition INTO a repair label opens a window and
    the next transition to another label closes it. Returns
    ``(still_open, closed)`` where ``still_open`` maps asset id to the time the unit
    entered repair and ``closed`` lists ``(asset_id, entered_at)`` pairs.
    """
    still_open, closed = {}, []
    for asset_id, pk, when in _status_transitions(repair_pks):
        if pk in repair_pks:
            still_open.setdefault(asset_id, when)
        elif asset_id in still_open:
            closed.append((asset_id, still_open.pop(asset_id)))
    return still_open, closed


def _status_transitions(repair_pks):
    """Yield ``(asset_id, recorded_status_pk, time)`` for every asset status change."""
    del repair_pks  # the caller filters; this reader walks the whole timeline
    content_type = ContentType.objects.get_for_model(Asset)
    changes = (
        ObjectChange._base_manager.filter(changed_object_type=content_type, action="update")
        .order_by("changed_object_id", "time", "pk")
        .values("changed_object_id", "time", "postchange_data")
    )
    for row in changes.iterator():
        status_pk = _recorded_status_pk(row["postchange_data"])
        if status_pk is None:
            continue
        yield row["changed_object_id"], status_pk, row["time"]


def _repair_intervals_by_asset(repair_pks):
    """Map asset id to the closed repair windows recorded for it, as date ranges.

    A still-open window (the unit is in repair now) is included up to "today" so an
    in-progress repair can document work; a closed window is used as recorded.
    """
    still_open, closed = _repair_windows(repair_pks)
    intervals = {}
    for asset_id, started in closed:
        end = _last_status_change_time(asset_id)
        if end is None:
            end = started
        intervals.setdefault(asset_id, []).append((started.date(), end.date()))
    today = datetime.date.today()
    for asset_id, started in still_open.items():
        intervals.setdefault(asset_id, []).append((started.date(), today))
    return intervals


def _last_status_change_time(asset_id):
    content_type = ContentType.objects.get_for_model(Asset)
    return (
        ObjectChange._base_manager.filter(changed_object_type=content_type, changed_object_id=asset_id)
        .order_by("-time", "-pk")
        .values_list("time", flat=True)
        .first()
    )


def _held_during_repair(asset, started):
    """True when a person held ``asset`` at the moment a repair began.

    This is the temporal question, and asking it any other way produces false
    passes: "was there ever an assignment checked in before the repair" is
    satisfied by an assignment that closed months earlier while a *different*
    holder kept the unit through the whole repair. The correct test is whether an
    assignment to a person was open at ``started`` — checked out at or before it and
    not yet checked in.
    """
    return (
        asset.assignments.filter(
            assigned_user__isnull=False,
            checked_out_at__lte=started,
        )
        .filter(Q(checked_in_at__isnull=True) | Q(checked_in_at__gt=started))
        .exists()
    )


def _first_repair_contradiction(repair_pks):
    """Return ``(asset, reason)`` for the first repair/assignment contradiction."""
    still_open, closed = _repair_windows(repair_pks)
    for asset_id, started in still_open.items():
        asset = Asset._base_manager.filter(pk=asset_id).first()
        # A unit still flagged as being in repair must not still be held by someone.
        if asset is not None and _is_held(asset):
            return asset, f"is recorded in repair since {started:%Y-%m-%d} but is still actively assigned"
    for asset_id, started in closed:
        asset = Asset._base_manager.filter(pk=asset_id).first()
        if asset is None:
            continue
        if _held_during_repair(asset, started):
            return asset, f"was recorded in repair from {started:%Y-%m-%d} while a person still held it"
    return None


def _check_repair_history_matches_assignments():
    """No unit may be recorded as repaired while a person assignment was open.

    The product cannot move an assigned unit into repair without a check-in, so a
    change log that walks a held unit through the repair label is a state the
    application itself would never produce. For every repair window in the change
    log, no person assignment may have been open when the window opened.
    """
    repair_pks = _repair_label_pks()
    if not repair_pks:
        return
    contradiction = _first_repair_contradiction(repair_pks)
    if contradiction is not None:
        asset, reason = contradiction
        raise CommandError(f"Seed operational invariant failed: asset {asset.pk} ({asset.asset_tag}) {reason}.")


def _check_maintenance_matches_timeline():
    """A maintenance record must be internally consistent and belong to an episode.

    A record that claims to be completed without a completion date, completes before
    it starts, or describes out-of-service work that belongs to no repair episode is
    paperwork the asset's own timeline cannot corroborate.
    """
    incomplete = (
        AssetMaintenance._base_manager.filter(
            status=MaintenanceStatusChoices.COMPLETED,
            completion_date__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    if incomplete is not None:
        raise CommandError(
            "Seed operational invariant failed: maintenance record "
            f"{incomplete.pk} is completed but has no completion date."
        )

    inverted = AssetMaintenance._base_manager.filter(Q(completion_date__lt=F("start_date"))).order_by("pk").first()
    if inverted is not None:
        raise CommandError(
            "Seed operational invariant failed: maintenance record "
            f"{inverted.pk} completes ({inverted.completion_date}) before it starts "
            f"({inverted.start_date})."
        )

    ungrouped = (
        AssetMaintenance._base_manager.filter(
            maintenance_type__in=OUT_OF_SERVICE_TYPES,
            episode__isnull=True,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    if ungrouped is not None:
        raise CommandError(
            "Seed operational invariant failed: maintenance record "
            f"{ungrouped.pk} is out-of-service work that belongs to no repair episode."
        )

    _check_out_of_service_work_matches_repair_windows()


def _check_out_of_service_work_matches_repair_windows():
    """Every out-of-service record must sit inside a real repair window.

    An episode only *groups* records; it carries no lifecycle state, so belonging to
    one is not evidence that the unit ever went out of service. This is the check
    that actually closes the #506 defect ("repair" records on assets that never left
    deployable state): the record's service interval must overlap a repair window
    that the change log recorded for the same asset.
    """
    repair_pks = _repair_label_pks()
    if not repair_pks:
        return
    windows = _repair_intervals_by_asset(repair_pks)
    out_of_service = (
        AssetMaintenance._base_manager.filter(
            maintenance_type__in=OUT_OF_SERVICE_TYPES,
            deleted_at__isnull=True,
        )
        .select_related("asset")
        .order_by("pk")
    )
    for record in out_of_service:
        intervals = windows.get(record.asset_id) or []
        end = record.completion_date or record.start_date
        overlapping = [i for i in intervals if i[0] <= end and i[1] >= record.start_date]
        if not overlapping:
            raise CommandError(
                "Seed operational invariant failed: maintenance record "
                f"{record.pk} documents out-of-service work on asset {record.asset.pk} "
                f"between {record.start_date} and {end}, but the asset's change log "
                "records no repair in that period."
            )


def _check_approved_requests_allocated():
    """A seeded asset-type request must be coherent with the product's claim path.

    Scoped deliberately to single-asset requests (``asset_type`` set, no inventory
    item, not a group). Mirrors ``RequestClaimView``: claiming an approved request
    without an allocated unit raises "No asset has been allocated to this request.",
    so a seeded approved asset request without one is a demo dead end. States outside
    this scope are legitimate product states and are left alone — a pending request
    may name a concrete asset (``AssetRequestForm`` offers "Specific Asset (by
    Tag)"), and an approved accessory, component or consumable request legitimately
    carries ``asset=NULL``.
    """
    scoped = _single_asset_requests()

    unallocated = (
        scoped.filter(
            status=RequestStatusChoices.APPROVED,
            asset__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    if unallocated is not None:
        raise CommandError(
            "Seed operational invariant failed: asset request "
            f"{unallocated.pk} is approved but has no allocated asset, so it can never "
            "be claimed."
        )

    tenantless = scoped.filter(tenant__isnull=True).order_by("pk").first()
    if tenantless is not None:
        raise CommandError(
            "Seed operational invariant failed: asset request "
            f"{tenantless.pk} has no tenant, so it is invisible in every tenant scope."
        )

    for request in scoped.filter(asset__isnull=False).select_related("asset", "asset__status").order_by("pk")[:200]:
        if request.tenant_id and request.asset.tenant_id != request.tenant_id:
            raise CommandError(
                "Seed operational invariant failed: asset request "
                f"{request.pk} has an allocated asset from another tenant."
            )
        if request.asset.status.type not in ("deployable", "deployed"):
            raise CommandError(
                "Seed operational invariant failed: asset request "
                f"{request.pk} allocated asset {request.asset.pk} is in a "
                f"{request.asset.status.type} status, so the claim cannot complete."
            )


def _single_asset_requests():
    """The seeded request shape this check speaks about: one requested asset type."""
    return AssetRequest._base_manager.filter(
        deleted_at__isnull=True,
        asset_type__isnull=False,
        is_group=False,
        component__isnull=True,
        accessory__isnull=True,
        consumable__isnull=True,
    )
