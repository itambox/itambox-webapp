# Consumable Stocks

**Consumable Stock** records track the physical quantities of bulk consumable items stored at physical Site Locations.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Consumable** | The parent consumable catalog item being tracked. | Foreign Key | Yes |
| **Location** | The specific facility Site Location room containing the inventory. | Foreign Key | Yes |
| **Quantity** | Current inventory level in stock. | Integer | Yes |
