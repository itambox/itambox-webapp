# Purchase Orders

A **Purchase Order (PO)** represents a procurement request dispatched to a supplier. It tracks the purchasing lifecycle from draft proposal to delivery receipt.

## Fields

### Created By

The user who registered the PO.

### Currency

ISO 4217 code. Leave blank to use the tenant default currency.

### Destination Location

The physical site/location receiving the items.

**Required:** Yes.

### Expected Delivery Date

Anticipated shipment arrival date.

### Notes

Optional comments or details.

### Order Date

Date the order was formally placed.

### Order Number

Unique procurement transaction ID.

**Required:** Yes.

### Status

Lifecycle state: `draft`, `approved`, `ordered`, `partial`, `received`, `cancelled`.

**Required:** Yes.

### Supplier

The vendor supplying the items.

**Required:** Yes.

### Tenant

Optional tenant scope for this PO.


## Workflow & Constraints

* **Order Number Uniqueness**: Unique per active tenant (soft-delete-aware).
* **Segregation of Duties**: The user approving the PO must not be the one who created it (`created_by`).
* **Approval and receiving permissions**: Approval and receiving require the corresponding ITAMbox permissions.
