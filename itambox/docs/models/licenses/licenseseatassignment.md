# License Seat Assignments

A **License Seat Assignment** documents the allocation of a single license entitlement seat to either a physical **Asset** (device-based licensing) or an **Asset Holder** (user-based licensing).

---

## Assignment Targets & Rules

Under compliance and database-level checking constraints, a license seat assignment must satisfy the following:
- **Mutual Exclusion**: A seat must be assigned to either a physical Asset OR an Asset Holder. It cannot be assigned to both, nor can it be left blank.
- **Seat Counts**: Assigning seats decreases the parent License's `available_seats` count. Seat assignments are blocked if the license runs out of available seats.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **License** | The software entitlement from which the seat is drawn. | Foreign Key | Yes |
| **Asset** | The physical hardware device receiving the license seat. | Foreign Key | No |
| **Assigned Holder** | The user or contractor profile receiving the license seat. | Foreign Key | No |
| **Assigned Date** | Timestamp when the seat was allocated. | DateTime | Yes (Auto) |
| **Notes** | Optional details outlining allocation details or subscription terms. | Text | No |
