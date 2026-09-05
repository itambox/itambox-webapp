# SaaS Subscriptions

A **Subscription** represents a recurring service agreement such as SaaS, support, maintenance, or a lease. It records the commercial terms, ownership, seat quantity, renewal date, and lifecycle state that ITAMbox uses for operational tracking.

For day-to-day workflows, see [Software, Licenses & Maintenance](../../usage/software-catalog-and-maintenance.md).

## Fields

### Name

The subscription name shown to operators, such as `Microsoft 365 Business Premium`.

**Required:** Yes.

### Provider

The provider or vendor supplying the service.

**Required:** Yes.

### Subscription Type

The kind of agreement, such as SaaS, support, maintenance, lease, or other.

### Status

The current ITAMbox lifecycle state: Active, Suspended, Cancelled, or Expired. Use the dedicated lifecycle actions rather than editing status directly.

### Start Date

When the current subscription term began, when known.

### Next Renewal Date

The date used by ITAMbox for renewal reminders and expiry evaluation.

### Renewal Cost

The cost recorded for one renewal period.

### Currency

The currency used for the recorded renewal cost.

### Billing Cycle

How often the vendor bills for the agreement, such as monthly, quarterly, annual, multi-year, or one-time.

### Term

The contractual term length in months, when applicable.

### Vendor Contract Auto-Renews

Records whether the vendor contract is expected to renew automatically. This is informational. ITAMbox does not renew the vendor contract or charge the provider on your behalf.

### Licensed Quantity

The number of users, seats, or devices covered by the agreement when quantity tracking applies.

### Contract Reference

A contract number, purchase-order reference, quote identifier, or similar external reference.

### Cost Center

The financial cost center responsible for the subscription.

### Cancellation Date

The date recorded when the subscription is cancelled.

### Owner

The ITAMbox user responsible for the subscription.

### Description

A description of the service or coverage.

### Notes

Internal operational notes.

### Tenant

The tenant that owns and scopes the subscription.

## Lifecycle Behavior

Use **Suspend**, **Resume**, **Renew**, and **Cancel** for lifecycle changes. Normal editing and generic API updates do not provide an alternative path for directly writing lifecycle state or cancellation dates.

Renewal records the new terms and next renewal date in ITAMbox. It does not perform vendor billing or payment processing. Scheduled evaluation can mark an active subscription expired after its renewal boundary and can generate renewal reminders according to current configuration.
