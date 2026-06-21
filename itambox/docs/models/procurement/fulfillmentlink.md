# Fulfillment Links

A **Fulfillment Link** bridges a pending asset request (requisition) with a specific Purchase Order Line, reserving a portion of the incoming shipment for that user request.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Request** | The user requisition requesting hardware. | Foreign Key | Yes |
| **Purchase Order Line** | The incoming PO line supplying the items. | Foreign Key | Yes |
| **Qty Allocated** | The quantity reserved for this request. | Integer | Yes |
| **Tenant** | Optional tenant scope. | Foreign Key | No |

## Constraints

* **Unique Mapping**: A unique constraint protects `(asset_request, purchase_order_line)` to ensure multiple allocations cannot conflict.
* **Auto-Release**: Cancelling a Purchase Order automatically deallocates linked fulfillment links and moves their requests back to `approved` status.
