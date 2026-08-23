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
| **From Location** | The physical stock location selected by the component Check-out action. Target-only allocation create and asset quick-add leave this empty. | Foreign Key | No |
| **Notes** | The notes of the component allocation. | Text | No |
| **Qty** | The checkout quantity of the component allocation. | Integer | Yes |

## Automated Stock Control
* **Target-only allocation create / asset quick-add**: No source pool is accepted. The active allocation reduces availability without changing a stock row.
* **Component Check-out**: A concrete source pool is required, locked, validated, and deducted atomically. A cross-tenant source also records the exact live Resource Grant.
* **Check-in / soft deletion**: A source-backed allocation restores exactly its quantity to the original source pool. Target-only allocations do not change stock.
* **Updates**: The component, source, and destination are immutable after creation. Quantity and notes remain editable.
