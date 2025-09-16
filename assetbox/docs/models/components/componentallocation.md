# Component Allocations

A **Component Allocation** represents a physical installation mapping record, representing modular hardware parts allocated to a parent serialized asset (e.g. installing `2x Crucial 16GB DDR4 RAM` into `ASSET-000102` server).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Component** | The catalog modular part being allocated. | Foreign Key | Yes |
| **Asset** | The parent physical asset receiving the parts. | Foreign Key | Yes |
| **From Location** | The physical Site Location room warehouse from which the stock is pulled. | Foreign Key | No |
| **Quantity Allocated** | Quantity of components physically installed. | Integer | Yes |
| **Allocated At** | Timestamp of allocation execution. | DateTime | Yes |

## Automated Stock Control
* **On Creation**: Trigger signals automatically decrement the allocated quantity from the defined `From Location` warehouse stock.
* **On Deletion (Soft/Hard)**: Trigger signals restore the allocated quantity back to the `From Location` warehouse stock, preserving count integrity.
