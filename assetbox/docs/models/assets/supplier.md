# Suppliers

A **Supplier** represents a sales vendor, reseller, distributor, or procurement merchant from whom hardware systems, software licenses, accessories, or consumables are purchased (e.g. `CDW`, `Amazon Business`, `Dell Direct`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the supplier. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Website** | Supplier's homepage link. | URL | No |
| **Contact Email** | Primary vendor contact email. | Email | No |
| **Contact Phone** | Primary phone number. | String | No |
| **Address** | Physical corporate headquarters address. | Text | No |
| **Contact Name** | Primary account manager or sales representative name. | String | No |

## Use Cases
Suppliers are associated with assets, accessories, and licenses to track warranties, purchase channels, support SLA escalations, and total cost of ownership (TCO) across different vendors.
