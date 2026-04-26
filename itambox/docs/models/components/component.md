# Hardware Components

A **Component** represents a physical, modular hardware sub-assembly tracked inside your inventory catalog that is allocated directly to a parent serialized system rather than checked out to users (e.g. `Crucial 16GB DDR4 RAM`, `Samsung 990 Pro 1TB NVMe SSD`, `Intel Xeon Silver 4314 CPU`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique model name of the component (e.g. `16GB DDR4 SODIMM`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Manufacturer** | Hardware developer (e.g. `Crucial`). | Foreign Key | Yes |
| **Category** | The asset category. Must have `applies_to__component` enabled. | Foreign Key | Yes |
| **Part Number / SKU** | Global SKU identifier. | String | No |
| **Specs** | JSON dictionary storing specific technical properties (e.g. speed, latency). | JSON | No |
| **Min Stock Level** | Safety threshold trigger alert when stock levels drop below this count. | Integer | Yes |
| **Description** | Optional notes. | Text | No |

## Stock & Allocation Lifecycle
Components reside in local stock repositories before being physically installed into parent servers or workstations. Installing components registers an allocation, deducting quantities from the warehouse stock automatically.
