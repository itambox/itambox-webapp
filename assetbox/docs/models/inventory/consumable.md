# Consumables

A **Consumable** represents a bulk, non-serialized, non-returnable item that is permanently consumed upon checkout or deployment (e.g. `Thermal Paste MX-4`, `CR2032 Lithium Batteries`, `Printer Toner Cartridge`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the consumable. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Manufacturer** | The manufacturing vendor. | Foreign Key | Yes |
| **Category** | The asset category. Must have `applies_to__consumable` enabled. | Foreign Key | No |
| **Part Number / SKU** | Global SKU identifier. | String | No |
| **Safety Threshold** | Minimum stock count triggering alerts when inventory gets low. | Integer | Yes |
| **Allow Over-allocation** | Allows checkout allocation count to exceed stock capacity. | Boolean | Yes |

## Lifecycle Workflow
Consumables are permanently debited from Site Location stock repositories. Because they are not returnable, checkout transactions represent immediate consumption and cannot be returned (checked in) later.
