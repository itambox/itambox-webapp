# Consumable Consumptions

A **Consumable Consumption** (or assignment) logs the permanent debiting of bulk consumables from stock, assigning custody or cost tracking to specific `Asset Holders` or `Locations`.

## Fields

### Assigned Asset

Asset associated with this assignment.

### Assigned Date

Timestamp of consumption execution.

**Required:** Yes.

### Assigned Holder

The user profile occupying/utilizing the item.

### Assigned Location

The destination Site Location room.

### Consumable

The consumable item being assigned.

**Required:** Yes.

### From Location

The physical Site Location room warehouse from which the stock is pulled.

### Notes

Optional notes about this consumable consumption.

### Qty

Quantity issued by this assignment.

**Required:** Yes.


## Stock Deductions
* **On Creation**: If `From Location` is declared, saving a consumption record automatically decrements the `qty` from `ConsumableStock` at that location.
* **Non-Returnable**: Unlike accessories, deleting a consumption record does not return quantities to stock, as the items are assumed spent.
