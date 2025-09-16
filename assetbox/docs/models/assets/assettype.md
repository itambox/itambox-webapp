# Asset Types

An **Asset Type** represents a specific model of hardware manufactured by a vendor (e.g. `Dell Latitude 7440`, `Apple MacBook Pro 16-inch M3`, `Cisco Catalyst 9300`). It defines the baseline specifications, EOL limits, depreciation configurations, and custom metadata for all physical assets of this type.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Manufacturer** | The company that manufactured the hardware (e.g., `Apple`). | Foreign Key | Yes |
| **Model** | The specific model name or number (e.g., `MacBook Pro 16"`). | String | Yes |
| **Slug** | Auto-slug source concatenating Manufacturer + Model. | Slug | Yes |
| **Part Number / SKU** | The manufacturer's global SKU identifier. | String | No |
| **CPU** | Processor specifications (e.g., `Intel Core i7-1370P`). | String | No |
| **RAM (GB)** | Installed memory capacity in Gigabytes. | Integer | No |
| **Storage (GB)** | Total storage disk capacity in Gigabytes. | Integer | No |
| **Storage Type** | Choice of: `SSD`, `NVMe SSD`, `HDD`, `eMMC`. | Choice | No |
| **EOL (Months)** | Useful lifespan in months before End-of-Life replacement is due. | Integer | No |
| **Depreciation** | Straight-line depreciation rule template to apply. | Foreign Key | No |
| **Category** | The asset category containing checkout rules. | Foreign Key | No |
| **Requestable** | Allows end-users to request assets of this type. | Boolean | Yes |

## Speclist Inheritance
All physical assets inherit their base hardware specifications (RAM, CPU, Storage) from their defined **Asset Type**, eliminating redundant field editing across identical systems.
