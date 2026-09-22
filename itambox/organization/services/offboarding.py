"""Offboarding readiness: compose a departing person's outstanding obligations.

ITAMbox already tracks every signal that says whether a departing person is
"clear", but it is spread across six apps. This service composes those existing
capabilities into a single, **read-only** report:

- active :class:`~assets.models.AssetAssignment` rows,
- accessory / component / consumable checkouts,
- license seats,
- unaccepted custody receipts,
- open :class:`~assets.models.AssetRequest` rows (requester **and** assigned_user),
- active :class:`~assets.models.AssetReservation` rows,
- subscription assignments,
- active :class:`~organization.models.Membership` rows,
- the person's own login state (:class:`User.is_active`).

The report *lists*; it never mutates. It does not deactivate a user, revoke a
grant, or check anything in — those are the operator's actions, taken from the
links the report provides. Readiness is therefore a truthful "is there
anything left to deal with?" answer, not a claim that offboarding has happened.

This module is a domain service: it imports other apps' *models* (read-only),
which the architecture matrix permits for the domain-service layer. It owns no
presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from assets.choices import RequestStatusChoices
from assets.models import (
    AssetAssignment,
    AssetRequest,
    AssetReservation,
)
from assets.models.choices import ReservationStatusChoices
from compliance.models import CustodyReceipt
from inventory.models import (
    AccessoryAssignment,
    ComponentAllocation,
    ConsumableAssignment,
)
from licenses.models import LicenseSeatAssignment
from subscriptions.models import SubscriptionAssignment

from ..models import AssetHolder, Membership

# Open asset-request statuses: in-flight work that has not been fulfilled,
# denied, or cancelled. (FULFILLED / DENIED / CANCELLED are terminal.)
_OPEN_REQUEST_STATUSES = [
    RequestStatusChoices.PENDING,
    RequestStatusChoices.APPROVED,
    RequestStatusChoices.PROCUREMENT,
]

# Open reservation statuses: the exclusion-constraint treats only ACTIVE/PENDING
# rows as occupying the asset, and a reservation is "still active" while its
# window has not fully passed (end_date >= today).
_OPEN_RESERVATION_STATUSES = [
    ReservationStatusChoices.PENDING,
    ReservationStatusChoices.ACTIVE,
]


@dataclass(frozen=True)
class ObligationItem:
    """One outstanding obligation for a departing person.

    :param kind: stable machine slug (e.g. ``"asset_assignment"``).
    :param label: human label for the obligation class.
    :param description: a short, truthful description of this specific item.
    :param url: link to the underlying detail record.
    :param object_pk: the primary key of the underlying record.
    :param model_label: dotted ``"app.Model"`` of the underlying record.
    """

    kind: str
    label: str
    description: str
    url: str
    object_pk: int
    model_label: str


@dataclass
class OffboardingReport:
    """Aggregate, read-only view of a person's outstanding obligations.

    :param holder: the :class:`~organization.models.AssetHolder` in question.
    :param items: the outstanding obligations (may be empty).
    :param user_is_active: the person's login state, or ``None`` when the holder
        has no linked :class:`~users.models.User`. Informational — the report
        does not flip it.
    """

    holder: AssetHolder
    items: list[ObligationItem] = field(default_factory=list)
    user_is_active: Optional[bool] = None

    @property
    def is_clear(self) -> bool:
        """True when there is no outstanding obligation to deal with."""
        return not self.items

    def for_kind(self, kind: str) -> list[ObligationItem]:
        """Return the items of one obligation class."""
        return [item for item in self.items if item.kind == kind]

    def counts(self) -> dict[str, int]:
        """Return ``{kind: count}`` for the non-empty classes."""
        result: dict[str, int] = {}
        for item in self.items:
            result[item.kind] = result.get(item.kind, 0) + 1
        return result


def _holder_user(holder: AssetHolder):
    """The :class:`User` linked to the holder, or ``None``."""
    return holder.user


def _asset_assignment_items(holder: AssetHolder) -> list[ObligationItem]:
    """Active asset assignments handed to this holder."""
    items: list[ObligationItem] = []
    for assignment in AssetAssignment.objects.filter(assigned_user=holder, is_active=True).select_related(
        "asset",
    ):
        items.append(
            ObligationItem(
                kind="asset_assignment",
                label="Asset assignment",
                description=f"Assigned asset: {assignment.asset}",
                url=assignment.get_absolute_url(),
                object_pk=assignment.pk,
                model_label="assets.AssetAssignment",
            )
        )
    return items


def _accessory_items(holder: AssetHolder) -> list[ObligationItem]:
    """Accessory checkouts in this holder's name."""
    return [
        ObligationItem(
            kind="accessory_assignment",
            label="Accessory checkout",
            description=f"Checked-out accessory: {item.accessory}",
            url=item.accessory.get_absolute_url(),
            object_pk=item.pk,
            model_label="inventory.AccessoryAssignment",
        )
        for item in AccessoryAssignment.objects.filter(assigned_holder=holder).select_related("accessory")
    ]


def _component_items(holder: AssetHolder) -> list[ObligationItem]:
    """Component allocations in this holder's name."""
    return [
        ObligationItem(
            kind="component_allocation",
            label="Component allocation",
            description=f"Allocated component: {item.component}",
            url=item.component.get_absolute_url(),
            object_pk=item.pk,
            model_label="inventory.ComponentAllocation",
        )
        for item in ComponentAllocation.objects.filter(assigned_holder=holder).select_related("component")
    ]


def _consumable_items(holder: AssetHolder) -> list[ObligationItem]:
    """Consumable checkouts in this holder's name."""
    return [
        ObligationItem(
            kind="consumable_assignment",
            label="Consumable checkout",
            description=f"Checked-out consumable: {item.consumable}",
            url=item.consumable.get_absolute_url(),
            object_pk=item.pk,
            model_label="inventory.ConsumableAssignment",
        )
        for item in ConsumableAssignment.objects.filter(assigned_holder=holder).select_related("consumable")
    ]


def _license_items(holder: AssetHolder) -> list[ObligationItem]:
    """License seats assigned to this holder."""
    return [
        ObligationItem(
            kind="license_seat",
            label="License seat",
            description=f"License seat: {item.license}",
            url=item.get_absolute_url(),
            object_pk=item.pk,
            model_label="licenses.LicenseSeatAssignment",
        )
        for item in LicenseSeatAssignment.objects.filter(assigned_holder=holder).select_related("license")
    ]


def _custody_items(holder: AssetHolder) -> list[ObligationItem]:
    """Custody receipts for this holder that have not been accepted yet.

    ``CustodyReceipt`` keeps an intentionally *unscoped* default manager (its
    public bearer-token sign route must resolve a receipt by secret token
    regardless of tenant context). The holder belongs to one tenant, so scope
    on ``asset__tenant`` exactly as the holder detail view does.
    """
    receipts = CustodyReceipt.objects.filter(
        holder=holder,
        asset__tenant=holder.tenant,
        acceptance_status=CustodyReceipt.STATUS_PENDING,
    ).select_related("asset")
    return [
        ObligationItem(
            kind="custody_receipt",
            label="Custody receipt",
            description=f"Unaccepted custody receipt for: {receipt.asset}",
            url=receipt.get_absolute_url(),
            object_pk=receipt.pk,
            model_label="compliance.CustodyReceipt",
        )
        for receipt in receipts
    ]


def _request_items(holder: AssetHolder) -> list[ObligationItem]:
    """Open asset requests where this person is the requester *or* the assignee."""
    user = _holder_user(holder)
    qs = AssetRequest.objects.filter(status__in=_OPEN_REQUEST_STATUSES)
    if user is not None:
        qs = qs.filter(Q(requester=user) | Q(assigned_user=holder))
    else:
        qs = qs.filter(assigned_user=holder)
    items: list[ObligationItem] = []
    for request in qs.select_related("asset"):
        role = "requester" if (user is not None and request.requester_id == user.pk) else "assigned user"
        items.append(
            ObligationItem(
                kind="asset_request",
                label="Asset request",
                description=f"Open asset request ({role}): {request}",
                url=request.get_absolute_url(),
                object_pk=request.pk,
                model_label="assets.AssetRequest",
            )
        )
    return items


def _reservation_items(holder: AssetHolder) -> list[ObligationItem]:
    """Reservations of an asset that are still inside their window."""
    today = date.today()
    qs = AssetReservation.objects.filter(
        reserved_for=holder,
        status__in=_OPEN_RESERVATION_STATUSES,
        end_date__gte=today,
    ).select_related("asset")
    return [
        ObligationItem(
            kind="asset_reservation",
            label="Asset reservation",
            description=f"Reservation of {reservation.asset} until {reservation.end_date}",
            url=reservation.get_absolute_url(),
            object_pk=reservation.pk,
            model_label="assets.AssetReservation",
        )
        for reservation in qs
    ]


def _subscription_items(holder: AssetHolder) -> list[ObligationItem]:
    """Subscription assignments that cover this holder (GenericRelation target)."""
    holder_ct = ContentType.objects.get(app_label="organization", model="assetholder")
    return [
        ObligationItem(
            kind="subscription",
            label="Subscription",
            description=f"Subscription covering this holder: {assignment.subscription}",
            url=assignment.get_absolute_url(),
            object_pk=assignment.pk,
            model_label="subscriptions.SubscriptionAssignment",
        )
        for assignment in SubscriptionAssignment.objects.filter(
            content_type=holder_ct,
            object_id=holder.pk,
        ).select_related("subscription")
    ]


def _membership_items(holder: AssetHolder) -> list[ObligationItem]:
    """Active memberships binding this person's user to the holder's tenant."""
    user = _holder_user(holder)
    if user is None or holder.tenant_id is None:
        return []
    return [
        ObligationItem(
            kind="membership",
            label="Tenant membership",
            description=f"Active membership in {membership.tenant}",
            url=membership.get_absolute_url(),
            object_pk=membership.pk,
            model_label="organization.Membership",
        )
        for membership in Membership.objects.filter(user=user, tenant=holder.tenant, is_active=True)
    ]


def get_offboarding_report(holder: AssetHolder) -> OffboardingReport:
    """Compose the outstanding-obligations report for ``holder``.

    Read-only: no writes, no side effects. Tenant scoping is inherited from the
    current tenant context (the same contextvar the active view already set),
    so each class is limited to the active tenant exactly as the surrounding
    list/detail views are.
    """
    items: list[ObligationItem] = []
    items += _asset_assignment_items(holder)
    items += _accessory_items(holder)
    items += _component_items(holder)
    items += _consumable_items(holder)
    items += _license_items(holder)
    items += _custody_items(holder)
    items += _request_items(holder)
    items += _reservation_items(holder)
    items += _subscription_items(holder)
    items += _membership_items(holder)

    user = _holder_user(holder)
    user_is_active = user.is_active if user is not None else None
    return OffboardingReport(holder=holder, items=items, user_is_active=user_is_active)
