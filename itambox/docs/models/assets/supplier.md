# Suppliers

A **Supplier** is the single commercial vendor record used across ITAMbox. It can represent a sales vendor, reseller, distributor, procurement merchant, or SaaS company (e.g. `CDW`, `Amazon Business`, `Dell Direct`, `GitHub`). Reuse one Supplier record wherever the same vendor is involved.

Suppliers may be scoped to a tenant or a tenant group. A supplier with neither scope is global and can be shared across tenants. A supplier cannot be scoped to both a tenant and a tenant group.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Address** | Physical corporate headquarters address. | Text | No |
| **Account ID** | Optional customer account number with the supplier. | String | No |
| **Contacts** | The contacts of the supplier. | Many-to-Many | No |
| **Active** | Whether the supplier is available in selection lists. | Boolean | Yes |
| **Name** | Unique name of the supplier within its scope. | String | Yes |
| **Notes** | The notes of the supplier. | Text | No |
| **Admin Portal URL** | URL for the supplier's management or administration portal. | URL | No |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Tenant** | Optional tenant owning this supplier. A global supplier has no Tenant or Tenant Group. | Foreign Key | No |
| **Tenant Group** | Optional tenant group owning this supplier. | Foreign Key | No |
| **Website** | Supplier's homepage link. | URL | No |

## Use Cases
Procurement, warranties, licenses, inventory items, and subscriptions all reference Suppliers. This shared catalogue supports purchase channels, support escalations, and total cost of ownership (TCO) reporting across vendors. See [Commercial Vocabulary](../../usage/commercial-vocabulary.md) for the agreement boundary between Contracts and Subscriptions.
