# Component Allocations

A **Component Allocation** represents a physical installation mapping record, representing modular hardware parts allocated to a parent serialized asset (e.g. installing `2x Crucial 16GB DDR4 RAM` into `ASSET-000102` server).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Assigned Asset** | The assigned asset of the component allocation. | Foreign Key | No |
| **Assigned Date** | The assigned date of the component allocation. | Date Time | Yes |
| **Assigned Holder** | The assigned holder of the component allocation. | Foreign Key | No |
| **Assigned Location** | The assigned location of the component allocation. | Foreign Key | No |
| **Component** | The catalog modular part being allocated. | Foreign Key | Yes |
| **From Location** | The physical Site Location room warehouse from which the stock is pulled. | Foreign Key | No |
| **Notes** | The notes of the component allocation. | Text | No |
| **Qty** | The checkout quantity of the component allocation. | Integer | Yes |

## Automated Stock Control
* **On Creation**: Trigger signals automatically decrement the allocated quantity from the defined `From Location` warehouse stock.
* **On Deletion (Soft/Hard)**: Trigger signals restore the allocated quantity back to the `From Location` warehouse stock, preserving count integrity.
