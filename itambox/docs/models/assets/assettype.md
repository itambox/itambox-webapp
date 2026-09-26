# Asset Types

An **Asset Type** represents a specific model of hardware manufactured by a vendor (e.g. `Dell Latitude 7440`, `Apple MacBook Pro 16-inch M3`, `Cisco Catalyst 9300`). It defines the model-level specification values, EOL limits, depreciation configurations, and custom metadata for its physical assets.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Role** | The functional role of the asset (e.g. `Developer Laptop`). | Foreign Key | No |
| **Category** | The asset category containing checkout rules. | Foreign Key | No |
| **Comments** | The comments of the asset type. | Text | No |
| **Custom Fieldsets** | Ordered specification sections composed for this model. | Many-to-Many | No |
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

## Specifications

Asset Types compose reusable specification **Custom Fieldsets** into an explicit, ordered list of sections (see [Custom Fieldsets](../extras/customfieldset.md)). New types can start from their category's default sections; the composition stays fully editable afterwards.

The composed sections define the specification form of the model: model-level values are stored on the Asset Type and are not copied to its assets. Each asset records its own observed values per specification field, and values that become historical are retained. See [Custom Fields](../usage/custom-fields.md) for composition semantics, empty-versus-omitted behavior, and the API contract.
