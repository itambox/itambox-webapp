# Kit Items

A **Kit Item** represents an individual model category slot template inside a parent `Kit`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accessory** | Accessory peripheral catalog slot template (e.g. `Dell Wired Keyboard KB216`). | Foreign Key | No |
| **Asset Type** | The asset type / model of the kit item. | Foreign Key | No |
| **Component** | The component catalog item of the kit item. | Foreign Key | No |
| **Consumable** | Consumable catalog slot template (e.g. `Thermal Paste MX-4`). | Foreign Key | No |
| **Kit** | The parent kit template configuration. | Foreign Key | Yes |
| **License** | Software license seat template slot. | Foreign Key | No |
| **Quantity** | Quantity to checkout (applicable to Accessories and Consumables). | Integer | Yes |

## Constraints
* A kit item must declare exactly one slot target. It cannot select more than one target (must be either an `Asset Type` OR `Accessory` OR `License` OR `Consumable`).
