# Accessories

An **Accessory** represents a bulk, non-serialized, returnable peripheral tracked in inventory that is checked out to users or locations (e.g. `Dell Wired Keyboard KB216`, `Logitech USB Mouse M100`, `HDMI Video Adapter`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the accessory peripheral. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Manufacturer** | The manufacturer vendor. | Foreign Key | Yes |
| **Category** | The asset category. Must have `applies_to__accessory` enabled. | Foreign Key | No |
| **Supplier** | The procurement vendor. | Foreign Key | No |
| **Part Number / SKU** | Global SKU code. | String | No |
| **Safety Threshold** | Minimum stock count triggering alerts when inventory gets low. | Integer | Yes |
| **Allow Over-allocation** | Allows checkout allocation count to exceed stock capacity. | Boolean | Yes |

## Lifecycle Workflow
* Accessories are checked out in discrete bulk counts to `AssetHolders` or `Locations`.
* Quantities are deducted from specific stock locations during the checkout process and returned upon check-in.
