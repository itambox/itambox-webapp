"""Read-only builder for the asset lifecycle timeline (#504).

The timeline merges the records of an asset's lifecycle story into one
chronological list: maintenance records, warranties, reservations, disposals and
status changes. Records that carry an optional repair-episode link are grouped
under that episode — the group shows every record linked to the episode,
whichever asset of the story owns it, so one page tells the whole story.
Everything else falls back to a flat chronological order, which is exactly what
assets without any episode links (all records written before the feature
existed) render as.

The builder is presentation-supporting read logic: it composes localized display
strings and never writes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext as _

from assets.models import AssetMaintenance, AssetReservation, RepairEpisode, StatusLabel
from assets.models.lifecycle import AssetDisposal
from core.models import ObjectChange

# Which lifecycle action kinds exist, their icon/colour for the template, and the
# stable intra-day ordering (a repair starts before its loan, a disposal closes
# the story).
_KIND_MAINTENANCE = "maintenance"
_KIND_WARRANTY = "warranty"
_KIND_RESERVATION = "reservation"
_KIND_DISPOSAL = "disposal"
_KIND_STATUS = "status"

_KIND_ORDER = {
    _KIND_MAINTENANCE: 0,
    _KIND_WARRANTY: 1,
    _KIND_RESERVATION: 2,
    _KIND_STATUS: 3,
    _KIND_DISPOSAL: 4,
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
    episode_id: int | None = None


@dataclass(frozen=True)
class TimelineGroup:
    """The events of one repair episode, in chronological order."""

    episode: RepairEpisode
    events: list[TimelineEvent]


@dataclass(frozen=True)
class AssetTimeline:
    """Grouped episode events plus the ungrouped chronological fallback."""

    groups: list[TimelineGroup] = field(default_factory=list)
    ungrouped: list[TimelineEvent] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(group.events) for group in self.groups) + len(self.ungrouped)

    @property
    def has_episodes(self) -> bool:
        return bool(self.groups)


def build_asset_timeline(asset) -> AssetTimeline:
    """Collect and group the timeline events for one asset."""
    own_events = _collect_asset_events(asset)
    episodes = _episodes_for_asset(asset)
    if not episodes:
        return AssetTimeline(ungrouped=_sorted_events(own_events))

    grouped: dict[int, list[TimelineEvent]] = {}
    seen: set[tuple[str, str]] = set()
    ungrouped_own: list[TimelineEvent] = []
    episode_pks = {episode.pk for episode in episodes}
    for event in own_events:
        if event.episode_id is not None and event.episode_id in episode_pks:
            grouped.setdefault(event.episode_id, []).append(event)
            seen.add(_event_key(event))
        else:
            ungrouped_own.append(event)

    for event in _collect_episode_events(episodes, asset):
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(event.episode_id, []).append(event)

    groups = [
        TimelineGroup(episode=episode, events=_sorted_events(grouped.get(episode.pk, []))) for episode in episodes
    ]
    groups.sort(key=_group_sort_date)
    return AssetTimeline(groups=groups, ungrouped=_sorted_events(ungrouped_own))


def _event_key(event: TimelineEvent) -> tuple[str, str]:
    return (event.kind, event.url)


def _group_sort_date(group: TimelineGroup) -> datetime.date:
    if group.events:
        return group.events[0].date
    return group.episode.created_at.date()


def _sorted_events(events: list[TimelineEvent]) -> list[TimelineEvent]:
    return sorted(events, key=lambda event: (event.date, _KIND_ORDER.get(event.kind, 99)))


def _collect_asset_events(asset) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for maintenance in asset.maintenances.all():
        events.append(_maintenance_event(maintenance, episode_id=maintenance.episode_id))
    for warranty in asset.warranties.all():
        events.append(_warranty_event(warranty))
    for reservation in asset.reservations.all().select_related("reserved_for"):
        events.append(_reservation_event(reservation, episode_id=reservation.episode_id))
    for disposal in AssetDisposal.all_objects.filter(asset=asset):
        events.append(_disposal_event(disposal, episode_id=disposal.episode_id))
    events.extend(_status_events(asset))
    return events


def _collect_episode_events(episodes: list[RepairEpisode], asset) -> list[TimelineEvent]:
    """Every record linked to one of the asset's episodes, whichever asset owns it."""
    events: list[TimelineEvent] = []
    for maintenance in AssetMaintenance.objects.filter(episode__in=episodes).select_related("asset"):
        events.append(_maintenance_event(maintenance, page_asset=asset, episode_id=maintenance.episode_id))
    for reservation in AssetReservation.objects.filter(episode__in=episodes).select_related("asset", "reserved_for"):
        events.append(_reservation_event(reservation, page_asset=asset, episode_id=reservation.episode_id))
    for disposal in AssetDisposal.all_objects.filter(episode__in=episodes).select_related("asset"):
        events.append(_disposal_event(disposal, page_asset=asset, episode_id=disposal.episode_id))
    return events


def _asset_part(record, page_asset) -> list[str]:
    """Name the owning asset when a grouped record belongs to another asset of the story."""
    if page_asset is None or record.asset_id == page_asset.pk:
        return []
    return [_("Asset: %(asset)s") % {"asset": str(record.asset)}]


def _maintenance_event(maintenance: AssetMaintenance, page_asset=None, episode_id=None) -> TimelineEvent:
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
        episode_id=episode_id,
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


def _reservation_event(reservation: AssetReservation, page_asset=None, episode_id=None) -> TimelineEvent:
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
        episode_id=episode_id,
    )


def _disposal_event(disposal: AssetDisposal, page_asset=None, episode_id=None) -> TimelineEvent:
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
        episode_id=episode_id,
    )


def _status_events(asset) -> list[TimelineEvent]:
    """Status transitions from the changelog (including the in-repair labels)."""
    content_type = ContentType.objects.get_for_model(type(asset))
    rows = ObjectChange.objects.filter(
        changed_object_type=content_type,
        changed_object_id=asset.pk,
        action=_STATUS_CHANGE_ACTION,
        postchange_data__has_key="status",
    ).order_by("-time")[:_STATUS_SCAN_LIMIT]
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
    return dict(StatusLabel.all_objects.filter(pk__in=label_ids).values_list("pk", "name"))


def _episodes_for_asset(asset) -> list[RepairEpisode]:
    return list(
        RepairEpisode.objects.filter(Q(asset=asset) | Q(substitute_asset=asset))
        .select_related("asset", "substitute_asset")
        .order_by("-created_at")
    )
