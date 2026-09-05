# Asset Reservations

An **Asset Reservation** holds or books a physical asset for a specific AssetHolder within a defined date window, preventing double-booking and conflicts during planning or staging workflows.

---

## Fields

### Asset

The physical asset being reserved.

**Required:** Yes.

### Reserved For

The AssetHolder who is requesting or assigned the reservation.

### Start Date

The first day the reservation is active.

**Required:** Yes.

### End Date

The last day the reservation is active (inclusive: the asset is held through this day).

**Required:** Yes.

### Status

Current lifecycle state: `Pending`, `Active`, `Fulfilled`, or `Cancelled`.

**Required:** Yes.

### Created By

The user who created the reservation.

### Purpose

A brief explanation or reason for the reservation.

### Notes

Optional additional comments or terms.


---

## Overlap Prevention

Pending and Active reservations for the same asset cannot overlap. Reservation dates are inclusive: if one reservation ends on the same date another begins, the two reservations conflict. Use a later start date for the handoff.

Cancelled, fulfilled, or deleted reservations no longer block the date range. The end date must be the same as or later than the start date.
