# Fulfillment Links

A **Fulfillment Link** bridges an approved Asset Request with a specific Purchase Order Line, reserving a portion of the incoming shipment for that request.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Request** | The user request that needs purchased inventory. | Foreign Key | Yes |
| **Purchase Order Line** | The incoming PO line supplying the items. | Foreign Key | Yes |
| **Qty Allocated** | The quantity reserved for this request. | Integer | Yes |
| **Tenant** | Owning tenant. The database field remains nullable for legacy rows, but the active Asset Request procurement seam requires it. | Foreign Key | Yes for active links |

## Constraints

* **Unique Mapping**: A unique constraint protects `(asset_request, purchase_order_line)` to ensure multiple allocations cannot conflict.
* **Tenant ownership**: The link, Asset Request, purchase-order line, and purchase order must all be tenant-owned and belong to the same tenant. Tenant-less legacy rows cannot enter the active seam.
* **Multi-unit Asset Types**: A group parent produces one Purchase Order Line and one quantity-one link per child request. Partial receipts approve only the children that received assets; the parent remains in `procurement` until every child is ready.
* **Auto-Release**: Cancelling a Purchase Order automatically deallocates linked fulfillment links and moves their requests back to `approved` status.
