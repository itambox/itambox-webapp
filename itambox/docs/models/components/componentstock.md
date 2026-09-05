# Component Stocks

**Component Stock** records track the physical quantities of modular hardware components residing at specific Site Locations.

## Fields

### Component

The parent hardware catalog component being tracked.

**Required:** Yes.

### Location

The specific facility Site Location room containing the inventory.

**Required:** Yes.

### Quantity

Current inventory level in stock.

**Required:** Yes.


## Stock Deductions
Stocks are managed dynamically. Installing a component into an asset registers a `ComponentAllocation` record, triggering database triggers to decrement the matching quantity from stock at the defined location.
