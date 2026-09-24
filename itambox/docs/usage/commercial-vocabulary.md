# Commercial Vocabulary

This page fixes the commercial vocabulary used across ITAMbox so that Suppliers,
Contracts, and Subscriptions mean the same thing in forms, detail pages, reports,
and documentation.

## Commercial party records

- **Supplier** is the shared commercial vendor record for a sales vendor,
  reseller, distributor, procurement merchant, or SaaS vendor. Procurement,
  warranties, Licenses, inventory, and Subscriptions all reference this same
  catalogue. Suppliers can be scoped to a tenant or tenant group; a Supplier
  with neither scope is global. The catalogue also stores portal URL, account
  ID, and active state. See [Suppliers](../models/assets/supplier.md).

## Where to record an agreement

Record each agreement in exactly one module:

- **SaaS and cloud entitlement:** record as Subscription.
- **Support, maintenance, lease, warranty, SLA, or asset-covered service:** record as Contract.
- **Other recurring entitlement without asset/SLA coverage:** record as Subscription.
- **Other legal or commercial agreement:** record as Contract.

Rules that keep the boundary intact:

- Record one agreement in one module only. Do not create the same agreement as
  both a Contract and a Subscription.
- Existing Subscriptions that use support, maintenance, or lease types remain
  valid; those values are not removed or migrated.
- `Subscription.contract_reference` is an external vendor, PO, or agreement
  reference. `Subscription.linked_contract` is an optional operational link to
  a Procurement Contract. The link does not combine the modules or justify
  recording the same agreement in both.

See [Contracts & Purchase Orders](contracts-and-purchase-orders.md) and
[SaaS Subscriptions](../models/subscriptions/subscription.md).

## Warranty vendor data

The vendor of a warranty is a Supplier link. The previous free-text vendor field
was removed; warranties without a linked Supplier show no vendor row. See
[Warranties](../models/assets/warranty.md).

## Subscription quantity metrics

- **Agreement Entitled Quantity** is the number of seats, users, or devices
  entitled by the vendor agreement. This value is independent of linked License seats.
- **Linked License Seats** are the seats of Licenses linked to the subscription,
  shown as assigned and available figures. They are computed from the linked
  licenses and never change the agreement entitlement.

See [Reports & Exports](reports-and-exports.md) for the report columns that
surface these values.

## Existing data

The free-text warranty vendor field is removed. No conversion is performed:
existing free-text values are not carried over to the Supplier link.
