# Consumable Consumptions

A **Consumable Consumption** (or assignment) logs the permanent debiting of bulk consumables from stock, assigning custody or cost tracking to specific `Asset Holders` or `Locations`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Consumable** | The consumable item being assigned. | Foreign Key | Yes |
| **Assigned Holder** | The user profile occupying/utilizing the item. | Foreign Key | No |
| **Assigned Location** | The destination Site Location room. | Foreign Key | No |
| **From Location** | The physical Site Location room warehouse from which the stock is pulled. | Foreign Key | No |
| **Consumed Quantity** | Quantity permanently checked out. | Integer | Yes |
| **Assigned Date** | Timestamp of consumption execution. | DateTime | Yes |

## Stock Deductions
* **On Creation**: If `From Location` is declared, saving a consumption record automatically decrements the `qty` from `ConsumableStock` at that location.
* **Non-Returnable**: Unlike accessories, deleting a consumption record does not return quantities to stock, as the items are assumed spent.
