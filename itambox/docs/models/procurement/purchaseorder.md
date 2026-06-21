# Purchase Orders

A **Purchase Order (PO)** represents a procurement request dispatched to a supplier. It tracks the purchasing lifecycle from draft proposal to delivery receipt.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Created By** | The user who registered the PO. | Foreign Key | No |
| **Currency** | ISO 4217 code. Leave blank to use the tenant default currency. | Choice | No |
| **Destination Location** | The physical site/location receiving the items. | Foreign Key | Yes |
| **Expected Delivery Date** | Anticipated shipment arrival date. | Date | No |
| **Notes** | Optional comments or details. | Text | No |
| **Order Date** | Date the order was formally placed. | Date | No |
| **Order Number** | Unique procurement transaction ID. | String | Yes |
| **Status** | Lifecycle state: `draft`, `approved`, `ordered`, `partial`, `received`, `cancelled`. | Choice | Yes |
| **Supplier** | The vendor supplying the items. | Foreign Key | Yes |
| **Tenant** | Optional tenant scope for this PO. | Foreign Key | No |

## Workflow & Constraints

* **Order Number Uniqueness**: Unique per active tenant (soft-delete-aware).
* **Segregation of Duties**: The user approving the PO must not be the one who created it (`created_by`).
* **Custom Permissions**: Protected by custom Django permissions `approve_purchaseorder` and `receive_purchaseorder`.
