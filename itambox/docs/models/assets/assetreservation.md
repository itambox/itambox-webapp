# Asset Reservations

An **Asset Reservation** holds or books a physical asset for a specific AssetHolder within a defined date window, preventing double-booking and conflicts during planning or staging workflows.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical asset being reserved. | Foreign Key | Yes |
| **Reserved For** | The AssetHolder who is requesting or assigned the reservation. | Foreign Key | No |
| **Start Date** | The first day the reservation is active. | Date | Yes |
| **End Date** | The last day the reservation is active (inclusive — the asset is held through this day). | Date | Yes |
| **Status** | Current lifecycle state: `Pending`, `Active`, `Fulfilled`, or `Cancelled`. | Choice | Yes |
| **Created By** | The user who created the reservation. | Foreign Key | No |
| **Purpose** | A brief explanation or reason for the reservation. | String (255) | No |
| **Notes** | Optional additional comments or terms. | Text | No |

---

## Overlap Prevention (Double-Booking)

A PostgreSQL exclusion constraint (`assetreservation_no_overlap`) guarantees that no two active or pending reservations for the same asset can overlap in time. The date range is treated as **inclusive on both ends** (`[]`): two reservations that share a boundary day conflict, enforcing a gap-day handoff between holders.

The constraint requires the `btree_gist` extension and combines:
- Equality on `asset` (the same physical asset)
- Overlap operator (`&&`) on the `daterange(start_date, end_date, '[]')`

Only rows with `status IN ('active', 'pending')` and `deleted_at IS NULL` participate in the check.

## Validation

- `end_date` must be on or after `start_date`.
- Before saving an active/pending reservation, the model's `clean()` method performs a Python-level overlap check as defence-in-depth against the database constraint.
- Soft-deleting a reservation automatically releases the booked time window from active conflict detection.
