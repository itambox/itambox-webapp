# Component Stocks

**Component Stock** records track the physical quantities of modular hardware components residing at specific Site Locations.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Component** | The parent hardware catalog component being tracked. | Foreign Key | Yes |
| **Location** | The specific facility Site Location room containing the inventory. | Foreign Key | Yes |
| **Quantity** | Current inventory level in stock. | Integer | Yes |

## Stock Deductions
Stocks are managed dynamically. Installing a component into an asset registers a `ComponentAllocation` record, triggering database triggers to decrement the matching quantity from stock at the defined location.
