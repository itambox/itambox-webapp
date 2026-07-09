# Accessories

An **Accessory** represents a bulk, non-serialized, returnable peripheral tracked in inventory that is checked out to users or locations (e.g. `Dell Wired Keyboard KB216`, `Logitech USB Mouse M100`, `HDMI Video Adapter`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Allow Over-allocation** | Allows checkout allocation count to exceed stock capacity. | Boolean | Yes |
| **Category** | The asset category. Must have `applies_to__accessory` enabled. | Foreign Key | No |
| **Ean** | Barcode (EAN / UPC / GTIN) — scannable to open this item. | String | No |
| **Manufacturer** | The manufacturer vendor. | Foreign Key | Yes |
| **Safety Threshold** | Minimum stock count triggering alerts when inventory gets low. | Integer | Yes |
| **Name** | Unique name of the accessory peripheral. | String | Yes |
| **Notes** | The notes of the accessory. | Text | No |
| **Part Number** | SKU or manufacturer part number | String | No |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Supplier** | The procurement vendor. | Foreign Key | No |
| **Tenant** | The tenant of the accessory. | Foreign Key | No |

## Lifecycle Workflow
* Accessories are checked out in discrete bulk counts to `AssetHolders` or `Locations`.
* Quantities are deducted from specific stock locations during the checkout process and returned upon check-in.
