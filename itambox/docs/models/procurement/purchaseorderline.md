# Purchase Order Lines

A **Purchase Order Line** represents an individual line item on a Purchase Order. It is polymorphic, mapping exactly one item type per line.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accessory** | Accessory catalog type being purchased. | Foreign Key | No |
| **Asset Type** | Hardware catalog type being purchased. | Foreign Key | No |
| **Component** | Component catalog type being purchased. | Foreign Key | No |
| **Consumable** | Consumable catalog type being purchased. | Foreign Key | No |
| **License** | Software license catalog type being purchased. | Foreign Key | No |
| **Purchase Order** | The parent Purchase Order. | Foreign Key | Yes |
| **Qty Ordered** | The total quantity ordered. | Integer | Yes |
| **Qty Received** | The quantity received so far (defaults to 0). | Integer | Yes |
| **Tenant** | Optional tenant scope. | Foreign Key | No |
| **Unit Price** | The cost per unit. | Decimal | No |

## Constraints & Properties

* **Polymorphic Constraint**: A line must map to exactly one of the five item FK fields (`asset_type`, `component`, `accessory`, `consumable`, or `license`).
* **Qty Outstanding**: Computed property representing `qty_ordered` minus `qty_received`.
* **Total Cost**: Computed property representing `qty_ordered` times `unit_price`.
