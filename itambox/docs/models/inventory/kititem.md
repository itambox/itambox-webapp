# Kit Items

A **Kit Item** represents an individual model category slot template inside a parent `Kit`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Kit** | The parent kit template configuration. | Foreign Key | Yes |
| **Asset Type** | Hardware model category slot template (e.g. `Dell Latitude 7440`). | Foreign Key | No |
| **Accessory** | Accessory peripheral catalog slot template (e.g. `Dell Wired Keyboard KB216`). | Foreign Key | No |
| **License** | Software license seat template slot. | Foreign Key | No |
| **Consumable** | Consumable catalog slot template (e.g. `Thermal Paste MX-4`). | Foreign Key | No |
| **Quantity** | Quantity to checkout (applicable to Accessories and Consumables). | Integer | Yes |

## Constraints
* A kit item must declare exactly one slot target. It cannot select more than one target (must be either an `Asset Type` OR `Accessory` OR `License` OR `Consumable`).
