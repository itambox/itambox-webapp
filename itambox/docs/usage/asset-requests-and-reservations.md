# Asset Requests And Reservations

Requests and reservations solve different planning problems. A request asks for an item to be supplied. A reservation blocks a specific asset for a holder during a defined date range.

## Asset Requests

A request contains exactly one requested item category: an Asset or Asset Type, Component, Accessory, or Consumable. The request can record the requester, intended recipient, reason, dates, and approval/procurement state.

Typical request states are:

| Status | Meaning |
| --- | --- |
| Pending | Waiting for a decision or workflow action |
| Approved | Approved and ready for fulfillment or procurement |
| Awaiting Procurement | The approved need is being sourced |
| Denied | Request rejected |
| Fulfilled | The requested need has been completed |
| Cancelled | Request closed without fulfillment |

Pending requests can be approved, denied, cancelled, or fulfilled. Approved requests can be fulfilled, cancelled, or moved to procurement. A request in procurement can return to approved, be fulfilled, or be cancelled.

Where request auto-approval is configured, eligible new requests can move directly from Pending to Approved. The policy is deployment-controlled; see [Feature Activation](../configuration/feature-activation.md).

## Fulfillment And Procurement

An approved request can be fulfilled using available inventory or connected to procurement. When a requested asset is checked out through the fulfillment flow, ITAMbox can mark the related approved request Fulfilled.

Procurement records should reflect the actual purchasing workflow. See [Contracts and Purchase Orders](contracts-and-purchase-orders.md).

## Reservations

A reservation blocks one specific Asset for an optional Asset Holder between a start date and end date.

The date range uses **calendar-day inclusive semantics**. The end date is the last day the asset is held. Two Pending or Active reservations that share the same day conflict. For example, a reservation ending on May 10 conflicts with another starting on May 10. Use May 11 for the next non-overlapping reservation.

Pending and Active reservations participate in overlap protection. Fulfilled and Cancelled reservations do not block a new reservation.

Reservation states are Pending, Active, Fulfilled, and Cancelled. Check-out/check-in workflows can advance the real operational lifecycle; a reservation is not itself custody.

## Choosing Between Them

Use a **Request** when someone needs equipment or stock and the exact unit may not be known yet. Use a **Reservation** when one concrete asset must be held for a known period.

See [Asset Request](../models/assets/assetrequest.md) and [Asset Reservation](../models/assets/assetreservation.md) for field-level reference.
