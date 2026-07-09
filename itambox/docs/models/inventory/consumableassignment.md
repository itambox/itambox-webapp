# Consumable Consumptions

A **Consumable Consumption** (or assignment) logs the permanent debiting of bulk consumables from stock, assigning custody or cost tracking to specific `Asset Holders` or `Locations`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Assigned Asset** | The assigned asset of the consumable consumption. | Foreign Key | No |
| **Assigned Date** | Timestamp of consumption execution. | DateTime | Yes |
| **Assigned Holder** | The user profile occupying/utilizing the item. | Foreign Key | No |
| **Assigned Location** | The destination Site Location room. | Foreign Key | No |
| **Consumable** | The consumable item being assigned. | Foreign Key | Yes |
| **From Location** | The physical Site Location room warehouse from which the stock is pulled. | Foreign Key | No |
| **Notes** | The notes of the consumable consumption. | Text | No |
| **Qty** | The checkout quantity of the consumable consumption. | Integer | Yes |

## Stock Deductions
* **On Creation**: If `From Location` is declared, saving a consumption record automatically decrements the `qty` from `ConsumableStock` at that location.
* **Non-Returnable**: Unlike accessories, deleting a consumption record does not return quantities to stock, as the items are assumed spent.
