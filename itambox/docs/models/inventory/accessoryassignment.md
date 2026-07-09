# Accessory Assignments

An **Accessory Assignment** maps active bulk checkouts of non-serialized accessories to specific `Asset Holders` or `Locations`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accessory** | The accessory peripheral being assigned. | Foreign Key | Yes |
| **Assigned Asset** | The assigned asset of the accessory assignment. | Foreign Key | No |
| **Assigned Date** | Timestamp of allocation activation. | DateTime | Yes |
| **Assigned Holder** | The destination user profile. | Foreign Key | No |
| **Assigned Location** | The destination Site Location room. | Foreign Key | No |
| **From Location** | The physical Site Location room warehouse from which the stock is pulled. | Foreign Key | No |
| **Notes** | The notes of the accessory assignment. | Text | No |
| **Checkout Quantity** | Quantity checked out. | Integer | Yes |

## Stock Deductions
* **On Creation**: If `From Location` is declared, saving an assignment automatically decrements the `qty` from `AccessoryStock` at that location.
* **On Return (Deletion)**: Deleting an assignment automatically restores the quantity back to the `From Location` stock, ensuring count integrity.
