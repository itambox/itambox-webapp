# Contracts

A **Contract** represents a service agreement, hardware/software support agreement, SLA, lease, or warranty contract. It links commercial parameters with physical assets covered under the agreement.

## Fields

### Covered Assets

Many-to-many list of assets covered under this contract.

### Auto-Renew

Toggles whether the contract renews automatically.

**Required:** Yes.

### Billing Cycle

Cadence: `monthly`, `quarterly`, `annual`, `biannual`, `multi_year`, `onetime`.

**Required:** Yes.

### Contract Number

Unique contract identifier.

**Required:** Yes.

### Contract Type

Type of contract: `support`, `maintenance`, `lease`, `warranty`, `service`, `other`.

**Required:** Yes.

### Cost

Billing cost amount.

### Cost Center

Scopes financial cost allocation.

### Coverage Hours

SLA support coverage (e.g. `24x7`).

### Currency

Currency of the contract cost.

**Required:** Yes.

### End Date

Contract expiration date.

**Required:** Yes.

### Name

Display name of the contract (e.g. `Laptop Lease Q3`).

**Required:** Yes.

### Notes

Optional comments.

### Purchase Order

Optional linked Purchase Order.

### Renewal Date

Scheduled renewal window check date.

### SLA Resolution Time

SLA resolution metric (e.g. `1 business day`).

### SLA Response Time

SLA response metric (e.g. `4 business hours`).

### SLA Terms

Summary or full text of SLA rules.

### Start Date

Contract activation date.

**Required:** Yes.

### Status

Lifecycle state: `draft`, `active`, `expired`, `cancelled`.

**Required:** Yes.

### Supplier

The vendor providing the contract.

### Tenant

Optional tenant scope.


## Constraints & Properties

* **Unique Contract Number**: Unique across active contracts (soft-delete-aware).
* **Date validation**: The end date cannot be before the start date.
* **Days Until Expiry**: Calculated calendar days remaining before `end_date`.
* **Is Expiring Soon**: Boolean flag returning `True` when the contract expires within 30 days.
