# Consumables

A **Consumable** represents a bulk, non-serialized, non-returnable item that is permanently consumed upon checkout or deployment (e.g. `Thermal Paste MX-4`, `CR2032 Lithium Batteries`, `Printer Toner Cartridge`).

## Fields

### Allow Over-allocation

Allows checkout allocation count to exceed stock capacity.

**Required:** Yes.

### Category

The asset category. Must have `applies_to__consumable` enabled.

### Ean

Barcode (EAN / UPC / GTIN): scannable to open this item.

### Manufacturer

The manufacturing vendor.

**Required:** Yes.

### Safety Threshold

Minimum stock count triggering alerts when inventory gets low.

**Required:** Yes.

### Name

Unique name of the consumable.

**Required:** Yes.

### Notes

Optional notes about this consumable.

### Part Number

SKU or manufacturer part number

### Slug

URL-safe name representation.

**Required:** Yes.

### Supplier

Supplier associated with this consumable.

### Tenant

Tenant that owns or scopes this consumable.


## Lifecycle Workflow
Consumables are permanently debited from Site Location stock repositories. Because they are not returnable, checkout transactions represent immediate consumption and cannot be returned (checked in) later.
