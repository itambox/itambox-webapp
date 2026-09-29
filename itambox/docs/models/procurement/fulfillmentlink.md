# Fulfillment Links

A **Fulfillment Link** bridges an approved Asset Request with a specific Purchase Order Line, reserving a portion of the incoming shipment for that request.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Request** | The user request that needs purchased inventory. | Foreign Key | Yes |
| **Purchase Order Line** | The incoming PO line supplying the items. | Foreign Key | Yes |
| **Qty Allocated** | The quantity reserved for this request. | Integer | Yes |
| **Qty Received** | The received quantity attributed to this request. Blank on links that predate receipt attribution; those count as fully outstanding until their next full receipt. | Integer | No (blank on legacy links) |
| **Tenant** | Owning tenant. The database field remains nullable for legacy rows, but the active Asset Request procurement seam requires it. | Foreign Key | Yes for active links |

## Constraints

* **Unique Mapping**: A unique constraint protects `(asset_request, purchase_order_line)` to ensure multiple allocations cannot conflict.
* **Tenant ownership**: The link, Asset Request, purchase-order line, and purchase order must all be tenant-owned and belong to the same tenant. Tenant-less legacy rows cannot enter the active seam.
* **Multi-unit Asset Types**: A group parent produces one Purchase Order Line and one quantity-one link per child request. Each delivered unit is allocated to one child; partial receipts approve only the children that received an asset, and the parent remains in `procurement` until every child is ready.
* **Partial delivery accounting**: Receipts attribute the delivered quantity to the oldest open link on the line first. A genuinely quantity-bearing request (component, accessory, consumable) becomes `approved` only once its full reserved quantity has arrived; surplus units beyond the reserved demand stay as free stock. Requests for multiple serialised units must be split into request units before they can be linked.
* **Auto-Release**: Cancelling a Purchase Order automatically deallocates linked fulfillment links and moves their requests back to `approved` status. Cancelling a request unit closes its still-unreceived links while the delivered quantity stays recorded on the closed link and the purchase order line is left untouched.
