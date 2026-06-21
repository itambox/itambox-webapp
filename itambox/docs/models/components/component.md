# Hardware Components

A **Component** represents a physical, modular hardware sub-assembly tracked inside your inventory catalog that is allocated directly to a parent serialized system rather than checked out to users (e.g. `Crucial 16GB DDR4 RAM`, `Samsung 990 Pro 1TB NVMe SSD`, `Intel Xeon Silver 4314 CPU`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Allow Overallocate** | Allow checkout count to exceed stock capacity | Boolean | Yes |
| **Category** | The asset category. Must have `applies_to__component` enabled. | Foreign Key | Yes |
| **Ean** | Barcode (EAN / UPC / GTIN) — scannable to open this item. | String | No |
| **Manufacturer** | Hardware developer (e.g. `Crucial`). | Foreign Key | Yes |
| **Min Qty** | Alert threshold quantity | Integer | No |
| **Name** | Unique model name of the component (e.g. `16GB DDR4 SODIMM`). | String | Yes |
| **Notes** | The notes of the component (catalog). | Text | No |
| **Part Number** | SKU or manufacturer part number | String | No |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Specs** | JSON dictionary storing specific technical properties (e.g. speed, latency). | JSON | No |
| **Supplier** | The supplier of the component (catalog). | Foreign Key | No |
| **Tenant** | The tenant of the component (catalog). | Foreign Key | No |

## Stock & Allocation Lifecycle
Components reside in local stock repositories before being physically installed into parent servers or workstations. Installing components registers an allocation, deducting quantities from the warehouse stock automatically.
