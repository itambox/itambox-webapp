# Suppliers

A **Supplier** represents a sales vendor, reseller, distributor, or procurement merchant from whom hardware systems, software licenses, accessories, or consumables are purchased (e.g. `CDW`, `Amazon Business`, `Dell Direct`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Address** | Physical corporate headquarters address. | Text | No |
| **Contacts** | The contacts of the supplier. | Many-to-Many | No |
| **Name** | Unique name of the supplier. | String | Yes |
| **Notes** | The notes of the supplier. | Text | No |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Website** | Supplier's homepage link. | URL | No |

## Use Cases
Suppliers are associated with assets, accessories, licenses, contracts, maintenance records, warranties, and subscription provider profiles to track warranties, purchase channels, support SLA escalations, and total cost of ownership (TCO) across different vendors. See [Commercial Vocabulary](../../usage/commercial-vocabulary.md) for how Suppliers relate to Providers and warranty vendors.
