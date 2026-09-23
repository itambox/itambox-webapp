# Commercial Vocabulary

This page fixes the commercial vocabulary used across ITAMbox so that Suppliers,
Providers, Contracts, and Subscriptions mean the same thing in forms, detail
pages, reports, and documentation.

## Commercial party records

- **Supplier** is the shared commercial vendor record: a sales vendor, reseller,
  distributor, or procurement merchant. Assets, Contracts, Licenses, Maintenance
  records, warranties, and subscription provider profiles can all point to the
  same Supplier. See [Suppliers](../models/assets/supplier.md).
- **Provider** is a scoped subscription profile: a cloud platform, software
  vendor, or web application hosting a subscription service. A Provider can be
  linked to a Supplier so both records reuse one vendor identity instead of
  duplicating the name. See [SaaS Providers](../models/subscriptions/provider.md).

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
  reference. It does not link a Procurement Contract, and it must not be used
  to justify duplicating the same agreement.

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
