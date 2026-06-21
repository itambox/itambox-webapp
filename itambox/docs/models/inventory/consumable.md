# Consumables

A **Consumable** represents a bulk, non-serialized, non-returnable item that is permanently consumed upon checkout or deployment (e.g. `Thermal Paste MX-4`, `CR2032 Lithium Batteries`, `Printer Toner Cartridge`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Allow Over-allocation** | Allows checkout allocation count to exceed stock capacity. | Boolean | Yes |
| **Category** | The asset category. Must have `applies_to__consumable` enabled. | Foreign Key | No |
| **Ean** | Barcode (EAN / UPC / GTIN) — scannable to open this item. | String | No |
| **Manufacturer** | The manufacturing vendor. | Foreign Key | Yes |
| **Safety Threshold** | Minimum stock count triggering alerts when inventory gets low. | Integer | Yes |
| **Name** | Unique name of the consumable. | String | Yes |
| **Notes** | The notes of the consumable. | Text | No |
| **Part Number** | SKU or manufacturer part number | String | No |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Supplier** | The supplier of the consumable. | Foreign Key | No |
| **Tenant** | The tenant of the consumable. | Foreign Key | No |

## Lifecycle Workflow
Consumables are permanently debited from Site Location stock repositories. Because they are not returnable, checkout transactions represent immediate consumption and cannot be returned (checked in) later.
