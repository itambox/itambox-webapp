# Accessories

An **Accessory** represents a bulk, non-serialized, returnable peripheral tracked in inventory that is checked out to users or locations (e.g. `Dell Wired Keyboard KB216`, `Logitech USB Mouse M100`, `HDMI Video Adapter`).

## Fields

### Allow Over-allocation

Allows checkout allocation count to exceed stock capacity.

**Required:** Yes.

### Category

The asset category. Must have `applies_to__accessory` enabled.

### Ean

Barcode (EAN / UPC / GTIN): scannable to open this item.

### Manufacturer

The manufacturer vendor.

**Required:** Yes.

### Safety Threshold

Minimum stock count triggering alerts when inventory gets low.

**Required:** Yes.

### Name

Unique name of the accessory peripheral.

**Required:** Yes.

### Notes

Optional notes about this accessorie.

### Part Number

SKU or manufacturer part number

### Slug

URL-safe name representation.

**Required:** Yes.

### Supplier

The procurement vendor.

### Tenant

Tenant that owns or scopes this accessorie.


## Lifecycle Workflow
* Accessories are checked out in discrete bulk counts to `AssetHolders` or `Locations`.
* Quantities are deducted from specific stock locations during the checkout process and returned upon check-in.
