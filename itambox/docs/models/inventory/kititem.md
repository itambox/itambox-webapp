# Kit Items

A **Kit Item** represents an individual slot inside a parent `Kit` template. Each line item points to exactly one target — an Asset Type, Accessory, Software License, Consumable, or Component — and specifies a quantity to be checked out when the kit is fulfilled.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Kit** | The parent Kit template this item belongs to. | Foreign Key | Yes |
| **Asset Type** | The catalog model template to provision from stock (e.g. `MacBook Pro 16"`). Mutually exclusive with all other target fields. | Foreign Key | No |
| **Accessory** | An accessory catalog item to include (e.g. `Dell Wired Keyboard KB216`). Mutually exclusive with all other target fields. | Foreign Key | No |
| **License** | A software license seat to reserve. Mutually exclusive with all other target fields. | Foreign Key | No |
| **Consumable** | A consumable catalog item to deduct (e.g. `Thermal Paste MX-4`). Mutually exclusive with all other target fields. | Foreign Key | No |
| **Component** | A component catalog item to allocate. Mutually exclusive with all other target fields. | Foreign Key | No |
| **Quantity** | Number of units to checkout. Applies to Accessories, Consumables, and Components. Defaults to 1. | Integer | Yes |

---

## Polymorphic Target Constraints

A database-level check constraint (`chk_kit_item_single_target`) enforces that **exactly one** of the five target foreign keys is set — you cannot select multiple targets and you cannot leave all targets empty.

Python-level validation in `clean()` provides the same guard with descriptive error messages.

When a kit is checked out:
- **Asset Type** slots trigger a stock allocation from available assets matching that model.
- **Accessory** and **Component** slots deduct from inventory stock and create assignment records.
- **Consumable** slots decrement on-hand stock.
- **License** slots reserve a seat from the linked license pool.
