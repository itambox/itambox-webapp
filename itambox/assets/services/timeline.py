"""Read-only builder for the asset lifecycle timeline (#504, #644).

The timeline merges the records of an asset's lifecycle story into one
chronological list: maintenance records, warranties, reservations, assignments
and loans, disposals and status changes. The repair maintenance is the anchor of
a repair story: every record linked to it - the loan issued to the asset's holder
and the disposal that closes the unit out - is grouped under that maintenance,
whichever asset of the story owns it, so one page tells the whole story.
Everything else falls back to a flat chronological order, which is exactly what
assets without any repair link render as.

The builder is presentation-supporting read logic: it composes localized display
strings and never writes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext as _

from assets.models import AssetAssignment, AssetMaintenance, AssetReservation, StatusLabel
from assets.models.lifecycle import AssetDisposal
from core.managers import Scope
from core.models import ObjectChange

# Which lifecycle action kinds exist, their icon/colour for the template, and the
# stable intra-day ordering (a repair starts before its loan, a disposal closes
# the story).
_KIND_MAINTENANCE = "maintenance"
_KIND_WARRANTY = "warranty"
_KIND_RESERVATION = "reservation"
_KIND_ASSIGNMENT = "assignment"
_KIND_LOAN = "loan"
_KIND_DISPOSAL = "disposal"
_KIND_STATUS = "status"

_KIND_ORDER = {
    _KIND_MAINTENANCE: 0,
    _KIND_WARRANTY: 1,
    _KIND_RESERVATION: 2,
    _KIND_ASSIGNMENT: 3,
    _KIND_LOAN: 4,
    _KIND_STATUS: 5,
    _KIND_DISPOSAL: 6,
}

# The changelog action through which asset status changes are recorded.
_STATUS_CHANGE_ACTION = "update"
# Bound on how many changelog rows one asset page inspects for status changes;
# the changelog tab keeps the complete history.
_STATUS_SCAN_LIMIT = 200


@dataclass(frozen=True)
class TimelineEvent:
    """One dated fact of the asset's story."""

    date: datetime.date
    kind: str
    icon: str
    color: str
    label: str
    title: str
    parts: list[str] = field(default_factory=list)
    url: str = ""
    maintenance_id: int | None = None


@dataclass(frozen=True)
class TimelineGroup:
    """The records of one repair maintenance, in chronological order."""

    maintenance: AssetMaintenance
    substitute_assets: list
    events: list[TimelineEvent]


@dataclass(frozen=True)
class AssetTimeline:
    """Grouped repair events plus the ungrouped chronological fallback."""

    groups: list[TimelineGroup] = field(default_factory=list)
    ungrouped: list[TimelineEvent] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(group.events) for group in self.groups) + len(self.ungrouped)

    @property
    def has_groups(self) -> bool:
        return bool(self.groups)

    @property
    def recent_events(self) -> list[TimelineEvent]:
        """Return the five latest events without changing the repair grouping."""
        events = [event for group in self.groups for event in group.events]
        events.extend(self.ungrouped)
        return sorted(events, key=lambda event: event.date, reverse=True)[:5]


def build_asset_timeline(asset) -> AssetTimeline:
    """Collect and group the timeline events for one asset."""
    own_events = _collect_asset_events(asset)
    maintenances = _maintenances_for_asset(asset)
    if not maintenances:
        return AssetTimeline(ungrouped=_sorted_events(own_events))

    grouped: dict[int, list[TimelineEvent]] = {}
    seen: set[tuple[str, str, datetime.date]] = set()
    ungrouped_own: list[TimelineEvent] = []
    maintenance_pks = {maintenance.pk for maintenance in maintenances}
    for event in own_events:
        if event.maintenance_id is not None and event.maintenance_id in maintenance_pks:
            grouped.setdefault(event.maintenance_id, []).append(event)
            seen.add(_event_key(event))
        else:
            ungrouped_own.append(event)

    for event in _collect_linked_events(maintenances, asset):
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(event.maintenance_id, []).append(event)

    groups = [
        TimelineGroup(
            maintenance=maintenance,
            substitute_assets=_substitute_assets(maintenance),
            events=_sorted_events(grouped.get(maintenance.pk, [])),
        )
        for maintenance in maintenances
    ]
    groups.sort(key=_group_sort_date)
    return AssetTimeline(groups=groups, ungrouped=_sorted_events(ungrouped_own))


def _event_key(event: TimelineEvent) -> tuple[str, str, datetime.date]:
    return (event.kind, event.url, event.date)


def _group_sort_date(group: TimelineGroup) -> datetime.date:
    if group.events:
        return group.events[0].date
    return group.maintenance.start_date


def _sorted_events(events: list[TimelineEvent]) -> list[TimelineEvent]:
    return sorted(events, key=lambda event: (event.date, _KIND_ORDER.get(event.kind, 99)))


def _collect_asset_events(asset) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for maintenance in AssetMaintenance.objects.for_scope(Scope.current()).filter(asset=asset).select_related("asset"):
        events.append(_maintenance_event(maintenance))
    for warranty in asset.warranties.all():
        events.append(_warranty_event(warranty))
    for reservation in asset.reservations.all().select_related("reserved_for"):
        events.append(_reservation_event(reservation))
    for assignment in asset.assignments.select_related("assigned_user", "assigned_location", "assigned_asset"):
        events.append(_assignment_event(assignment))
    # unscoped: the timeline shows a soft-deleted disposal as recycle-bin evidence
    for disposal in AssetDisposal.all_objects.filter(asset=asset).select_related("asset"):
        events.append(_disposal_event(disposal))
    events.extend(_status_events(asset))
    return events


def _collect_linked_events(maintenances: list[AssetMaintenance], asset) -> list[TimelineEvent]:
    """Every record linked to one of the asset's repair maintenances, whichever asset owns it."""
    events: list[TimelineEvent] = []
    for maintenance in maintenances:
        events.append(_maintenance_event(maintenance, page_asset=asset))
    linked_assignments = (
        AssetAssignment.objects.for_scope(Scope.current())
        .filter(maintenance__in=maintenances)
        .select_related("asset", "assigned_user", "assigned_location", "assigned_asset")
    )
    for assignment in linked_assignments:
        events.append(_assignment_event(assignment, page_asset=asset))
    # unscoped: a soft-deleted disposal linked to the repair is still its evidence
    linked_disposals = AssetDisposal.all_objects.filter(maintenance__in=maintenances).select_related("asset")
    for disposal in linked_disposals:
        events.append(_disposal_event(disposal, page_asset=asset))
    return events


def _substitute_assets(maintenance: AssetMaintenance) -> list:
    """The units that stood in for the repaired asset (the repair's loans)."""
    loans = (
        AssetAssignment.objects.for_scope(Scope.current())
        .filter(maintenance=maintenance, is_loan=True)
        .select_related("asset")
    )
    return [assignment.asset for assignment in loans]


def _asset_part(record, page_asset) -> list[str]:
    """Name the owning asset when a grouped record belongs to another asset of the story."""
    if page_asset is None or record.asset_id == page_asset.pk:
        return []
    return [_("Asset: %(asset)s") % {"asset": str(record.asset)}]


def _maintenance_event(maintenance: AssetMaintenance, page_asset=None) -> TimelineEvent:
    parts = [maintenance.get_status_display()]
    if maintenance.completion_date:
        parts.append(_("Completed %(date)s") % {"date": maintenance.completion_date.isoformat()})
    parts.extend(_asset_part(maintenance, page_asset))
    return TimelineEvent(
        date=maintenance.start_date,
        kind=_KIND_MAINTENANCE,
        icon="mdi-tools",
        color="red",
        label=_("Maintenance"),
        title=maintenance.get_maintenance_type_display(),
        parts=parts,
        url=maintenance.get_absolute_url(),
        maintenance_id=maintenance.pk,
    )


def _warranty_event(warranty) -> TimelineEvent:
    return TimelineEvent(
        date=warranty.start_date,
        kind=_KIND_WARRANTY,
        icon="mdi-shield-check",
        color="success",
        label=_("Warranty"),
        title=warranty.get_warranty_type_display(),
        parts=[_("Valid until %(date)s") % {"date": warranty.end_date.isoformat()}],
        url=warranty.get_absolute_url(),
    )


def _reservation_event(reservation: AssetReservation, page_asset=None) -> TimelineEvent:
    holder = str(reservation.reserved_for) if reservation.reserved_for_id else _("(no holder)")
    parts = [
        reservation.get_status_display(),
        _("Until %(date)s") % {"date": reservation.end_date.isoformat()},
    ]
    parts.extend(_asset_part(reservation, page_asset))
    return TimelineEvent(
        date=reservation.start_date,
        kind=_KIND_RESERVATION,
        icon="mdi-calendar-clock",
        color="blue",
        label=_("Reservation"),
        title=_("Reserved for %(holder)s") % {"holder": holder},
        parts=parts,
        url=reservation.get_absolute_url(),
    )


def _assignment_event(assignment: AssetAssignment, page_asset=None) -> TimelineEvent:
    """A checkout, or - when it is flagged as a loan - one loan of the story (#644)."""
    target = assignment.assigned_target
    parts = [_("Loan") if assignment.is_loan else _("Handover")]
    parts.append(_("Active") if assignment.is_active else _("Ended"))
    if assignment.due_date:
        parts.append(_("Due %(date)s") % {"date": assignment.due_date.isoformat()})
    if assignment.is_overdue:
        parts.append(_("Overdue"))
    if assignment.returned_at:
        parts.append(_("Returned %(date)s") % {"date": assignment.returned_at.isoformat()})
    parts.extend(_asset_part(assignment, page_asset))
    return TimelineEvent(
        date=assignment.checked_out_at.date(),
        kind=_KIND_LOAN if assignment.is_loan else _KIND_ASSIGNMENT,
        icon="mdi-account-arrow-right" if assignment.is_loan else "mdi-account-check",
        color="orange" if assignment.is_loan else "teal",
        label=_("Loan") if assignment.is_loan else _("Assignment"),
        title=_("Checked out to %(target)s") % {"target": target if target is not None else _("(no target)")},
        parts=parts,
        url=assignment.get_absolute_url(),
        maintenance_id=assignment.maintenance_id,
    )


def _disposal_event(disposal: AssetDisposal, page_asset=None) -> TimelineEvent:
    parts = []
    if disposal.is_cancelled:
        parts.append(_("Cancelled"))
    if disposal.deleted_at:
        parts.append(_("Previously deleted (recycle bin)"))
    if disposal.recipient:
        parts.append(_("Recipient: %(recipient)s") % {"recipient": disposal.recipient})
    parts.extend(_asset_part(disposal, page_asset))
    return TimelineEvent(
        date=disposal.disposal_date,
        kind=_KIND_DISPOSAL,
        icon="mdi-recycle",
        color="secondary",
        label=_("Disposal"),
        title=disposal.get_disposal_method_display(),
        parts=parts,
        url=disposal.get_absolute_url(),
        maintenance_id=disposal.maintenance_id,
    )


def _status_events(asset) -> list[TimelineEvent]:
    """Status transitions from the changelog (including the in-repair labels)."""
    content_type = ContentType.objects.get_for_model(type(asset))
    rows = (
        ObjectChange.objects.for_scope(Scope.current())
        .filter(
            changed_object_type=content_type,
            changed_object_id=asset.pk,
            action=_STATUS_CHANGE_ACTION,
            postchange_data__has_key="status",
        )
        .order_by("-time")[:_STATUS_SCAN_LIMIT]
    )
    transitions = []
    label_ids = set()
    for row in rows:
        previous = (row.prechange_data or {}).get("status")
        current = (row.postchange_data or {}).get("status")
        if previous == current:
            continue
        transitions.append((row.time, previous, current))
        label_ids.update(value for value in (previous, current) if value is not None)
    labels = _status_labels(label_ids)
    events = []
    for moment, previous, current in transitions:
        new_label = labels.get(current, _("(no status)")) if current is not None else _("(no status)")
        parts = []
        if previous is not None:
            parts.append(_("Previous status: %(status)s") % {"status": labels.get(previous, _("Unknown status"))})
        events.append(
            TimelineEvent(
                date=moment.date(),
                kind=_KIND_STATUS,
                icon="mdi-swap-horizontal",
                color="cyan",
                label=_("Status"),
                title=_("Status changed to %(status)s") % {"status": new_label},
                parts=parts,
                url=f"{asset.get_absolute_url()}?tab=changelog",
            )
        )
    return events


def _status_labels(label_ids: set) -> dict:
    if not label_ids:
        return {}
    # unscoped: historical status labels resolve even after soft deletion
    return dict(StatusLabel.all_objects.filter(pk__in=label_ids).values_list("pk", "name"))


def _maintenances_for_asset(asset) -> list[AssetMaintenance]:
    """The repair maintenances of the asset's story (#644).

    Its own repair maintenances, plus the repairs its loans belong to - the loaner of
    a repair shows the same group as the unit that was under repair. Work that does
    not take a unit out of service (an upgrade, a calibration) stays in the plain
    chronological list.
    """
    return list(
        AssetMaintenance.objects.for_scope(Scope.current())
        .filter(Q(asset=asset) | Q(assignments__asset=asset))
        .filter(maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR)
        .distinct()
        .select_related("asset")
        .order_by("-start_date")
    )
