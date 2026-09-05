# License Seat Assignments

A **License Seat Assignment** documents the allocation of a single license entitlement seat to either a physical **Asset** (device-based licensing) or an **Asset Holder** (user-based licensing).

---

## Assignment Targets & Rules

A license seat assignment must satisfy the following:
- **Mutual Exclusion**: A seat must be assigned to either a physical Asset OR an Asset Holder. It cannot be assigned to both, nor can it be left blank.
- **Seat Counts**: Assigning seats decreases the parent License's `available_seats` count. Seat assignments are blocked if the license runs out of available seats.

---

## Fields

### Asset

The physical hardware device receiving the license seat.

### Assigned Date

Timestamp when the seat was allocated.

**Required:** Yes (Auto).

### Assigned Holder

The user or contractor profile receiving the license seat.

### Installed Software

Discovered or recorded software installation associated with this seat assignment.

### License

The software entitlement from which the seat is drawn.

**Required:** Yes.

### Notes

Optional details outlining allocation details or subscription terms.
