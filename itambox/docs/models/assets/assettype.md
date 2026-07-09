# Asset Types

An **Asset Type** represents a specific model of hardware manufactured by a vendor (e.g. `Dell Latitude 7440`, `Apple MacBook Pro 16-inch M3`, `Cisco Catalyst 9300`). It defines the baseline specifications, EOL limits, depreciation configurations, and custom metadata for all physical assets of this type.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Role** | The functional role of the asset (e.g. `Developer Laptop`). | Foreign Key | No |
| **Category** | The asset category containing checkout rules. | Foreign Key | No |
| **Comments** | The comments of the asset type. | Text | No |
| **Custom Fieldset** | Associated custom fields grouping. | Foreign Key | No |
| **Depreciation** | Straight-line depreciation rule template to apply. | Foreign Key | No |
| **Description** | The description of the asset type. | Text | No |
| **Ean** | Barcode (EAN / UPC / GTIN) — scanning shows assets of this type. | String | No |
| **EOL (Months)** | Useful lifespan in months before End-of-Life replacement is due. | Integer | No |
| **Model Image** | Product image for this asset type model. | Image | No |
| **Manufacturer** | The company that manufactured the hardware (e.g., `Apple`). | Foreign Key | Yes |
| **Model** | The specific model name or number (e.g., `MacBook Pro 16"`). | String | Yes |
| **Part Number** | Manufacturer part number or SKU | String | No |
| **Requestable** | Allows end-users to request assets of this type. | Boolean | Yes |
| **Slug** | Auto-slug source concatenating Manufacturer + Model. | Slug | Yes |

## Speclist Inheritance
All physical assets inherit their base hardware specifications (RAM, CPU, Storage) from their defined **Asset Type**, eliminating redundant field editing across identical systems.
