# Fulfillment Links

A **Fulfillment Link** bridges an approved Asset Request with a specific Purchase Order Line, reserving a portion of the incoming shipment for that request.

## Fields

### Asset Request

The user request that needs purchased inventory.

**Required:** Yes.

### Purchase Order Line

The incoming PO line supplying the items.

**Required:** Yes.

### Qty Allocated

The quantity reserved for this request.

**Required:** Yes.

### Tenant

Owning tenant. The database field remains nullable for legacy rows, but the active Asset Request procurement seam requires it.

**Required:** Yes for active links.


## Constraints

* **Unique mapping**: The same asset request and purchase-order line are not linked more than once.
* **Tenant ownership**: The link, Asset Request, purchase-order line, and purchase order must all be tenant-owned and belong to the same tenant. Tenant-less legacy rows cannot enter the active seam.
* **Multi-unit Asset Types**: A group parent produces one Purchase Order Line and one quantity-one link per child request. Partial receipts approve only the children that received assets; the parent remains in `procurement` until every child is ready.
* **Auto-Release**: Cancelling a Purchase Order automatically deallocates linked fulfillment links and moves their requests back to `approved` status.
