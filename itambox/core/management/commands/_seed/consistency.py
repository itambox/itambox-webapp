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
   their status timeline, and left ~30 % without a completion date.
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

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import CommandError
from django.db.models import Count, F, Q

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetMaintenance, AssetRequest, StatusLabel
from assets.models.choices import MaintenanceStatusChoices
from core.models import ObjectChange
from procurement.models import PurchaseOrderLine

#: Maintenance types that describe work the product models as taking a unit out of
#: service. Such a record is the paperwork of an episode and must belong to one.
OUT_OF_SERVICE_TYPES = ("repair", "calibration")


def check_seed_operational_invariants():
    """Fail closed when a seeded operational story contradicts the product's rules.

    Called at the end of ``_seed_all`` so a regression in any of the four stories
    aborts the seed instead of shipping a dataset that misrepresents the product.
    """
    _check_po_receipts_materialised()
    _check_repair_history_matches_assignments()
    _check_maintenance_matches_timeline()
    _check_approved_requests_allocated()


def _check_po_receipts_materialised():
    """A received asset-type PO line must have as many assets as it reports received.

    Otherwise the PO screen, the asset ledger and any total-cost report built on the
    received quantity disagree about how many units were bought. Only lines that
    name an ``asset_type`` are in scope: an accessory or consumable line is received
    into stock, not into the asset ledger, and the seed does not create Assets for
    it at all.
    """
    mismatched = (
        PurchaseOrderLine._base_manager.filter(qty_received__gt=0, asset_type__isnull=False)
        .annotate(materialized=Count("assets", filter=Q(assets__deleted_at__isnull=True)))
        .filter(materialized__lt=F("qty_received"))
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
    content_type = ContentType.objects.get_for_model(Asset)
    changes = (
        ObjectChange._base_manager.filter(changed_object_type=content_type, action="update")
        .order_by("changed_object_id", "time", "pk")
        .values("changed_object_id", "time", "postchange_data")
    )
    still_open = {}
    closed = []
    for row in changes.iterator():
        asset_id = row["changed_object_id"]
        status_pk = _recorded_status_pk(row["postchange_data"])
        if status_pk is None:
            continue
        if status_pk in repair_pks:
            still_open.setdefault(asset_id, row["time"])
        elif asset_id in still_open:
            closed.append((asset_id, still_open.pop(asset_id)))
    return still_open, closed


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
        # The holder must have lost the device at or before the repair started.
        lapsed = asset.assignments.filter(
            assigned_user__isnull=False,
            checked_in_at__isnull=False,
            checked_in_at__lte=started,
        ).exists()
        if lapsed or not _is_held(asset):
            continue
        return asset, f"was recorded in repair from {started:%Y-%m-%d} without a preceding check-in"
    return None


def _check_repair_history_matches_assignments():
    """No unit may be recorded as repaired without its assignment first lapsing.

    The product cannot move an assigned unit into repair without a check-in, so a
    change log that walks a held unit through the repair label is a state the
    application itself would never produce. For every repair window in the change
    log, the asset's assignment must show a check-in at or before the window opened.
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


def _check_approved_requests_allocated():
    """An approved request must carry an allocated, claimable asset.

    Mirrors ``RequestClaimView``: claiming an approved request without an allocated
    unit raises "No asset has been allocated to this request.", so a seeded approved
    request without one is a demo dead end. A pending request, by contrast, must stay
    unallocated: the approver is the one who picks the unit.
    """
    unallocated = (
        AssetRequest._base_manager.filter(
            status=RequestStatusChoices.APPROVED,
            asset__isnull=True,
            deleted_at__isnull=True,
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

    pre_allocated = (
        AssetRequest._base_manager.filter(
            status=RequestStatusChoices.PENDING,
            asset__isnull=False,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    if pre_allocated is not None:
        raise CommandError(
            "Seed operational invariant failed: asset request "
            f"{pre_allocated.pk} is pending but already carries an allocated asset."
        )

    for request in (
        AssetRequest._base_manager.filter(asset__isnull=False, deleted_at__isnull=True)
        .select_related("asset", "asset__status")
        .order_by("pk")[:200]
    ):
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
