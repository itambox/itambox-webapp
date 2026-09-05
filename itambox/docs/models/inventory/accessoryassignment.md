# Accessory Assignments

An **Accessory Assignment** maps active bulk checkouts of non-serialized accessories to specific `Asset Holders` or `Locations`.

## Fields

### Accessory

The accessory peripheral being assigned.

**Required:** Yes.

### Assigned Asset

Asset associated with this assignment.

### Assigned Date

Timestamp of allocation activation.

**Required:** Yes.

### Assigned Holder

The destination user profile.

### Assigned Location

The destination Site Location room.

### From Location

The physical Site Location room warehouse from which the stock is pulled.

### Notes

Optional notes about this accessory assignment.

### Checkout Quantity

Quantity checked out.

**Required:** Yes.


## Stock Deductions
* **On Creation**: If `From Location` is declared, saving an assignment automatically decrements the `qty` from `AccessoryStock` at that location.
* **On Return (Deletion)**: Deleting an assignment automatically restores the quantity back to the `From Location` stock, ensuring count integrity.
