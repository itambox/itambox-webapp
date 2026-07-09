# Accessory Stocks

**Accessory Stock** records track the physical quantities of bulk, non-serialized accessory items stored at physical Site Locations.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accessory** | The parent accessory catalog peripheral item being tracked. | Foreign Key | Yes |
| **Location** | The specific facility Site Location room containing the inventory. | Foreign Key | Yes |
| **Quantity** | Current inventory level in stock. | Integer | Yes |

