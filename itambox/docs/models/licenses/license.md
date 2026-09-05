# Software Licenses

A **Software License** represents a specific purchase agreement, product key, or contract providing a defined number of seats for a software application (e.g. `Volume License Key - Office 2021`, `50x Adobe CC Enterprise Seats`).

## Fields

### Cost Center

Cost Center used for financial allocation of this software license.

### Currency

ISO 4217 code. Leave blank to use the tenant default currency.

### Expiration Date

Expiration date for term licenses or software maintenance.

### License Type

The type of entitlement (e.g. perpetual seat, subscription seat).

**Required:** Yes.

### Name

A descriptive name for this license purchase (e.g. `Office 365 E5 seats`).

**Required:** Yes.

### Notes

Optional internal notes regarding this purchase.

### Order Number

Associated purchase order or invoice reference.

### Product Key

The cryptographic activation code (stored symmetrically encrypted in DB).

### Purchase Cost

Total cost of license purchase.

### Purchase Date

Date of acquisition.

### Seats

Total number of seats available for allocation.

**Required:** Yes.

### Software

The application title from the Software Catalog.

**Required:** Yes.

### Subscription

Optional subscription (billing agreement) that funds this license; seats roll up to it.

### Supplier

The vendor or supplier of this license.

### Tenant

Cost center tenant owning this license.

### Version

Optional version constraint for this license entitlement (e.g. '2021', '16.x'). Informational only: reconciliation is performed at the Software level (version-agnostic).


## Checkout Seat Allocation
* License seats are checked out to `Asset Holders` or physical `Assets` (e.g. assigning a license seat to a developer workstation laptop).
* ITAMbox automatically tracks and displays available, checked out, and total seats.
