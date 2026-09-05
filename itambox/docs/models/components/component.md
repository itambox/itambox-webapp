# Hardware Components

A **Component** represents a physical, modular hardware sub-assembly tracked inside your inventory catalog that is allocated directly to a parent serialized system rather than checked out to users (e.g. `Crucial 16GB DDR4 RAM`, `Samsung 990 Pro 1TB NVMe SSD`, `Intel Xeon Silver 4314 CPU`).

## Fields

### Allow Overallocate

Allow checkout count to exceed stock capacity

**Required:** Yes.

### Category

The asset category. Must have `applies_to__component` enabled.

**Required:** Yes.

### Ean

Barcode (EAN / UPC / GTIN): scannable to open this item.

### Manufacturer

Hardware developer (e.g. `Crucial`).

**Required:** Yes.

### Min Qty

Alert threshold quantity

### Name

Unique model name of the component (e.g. `16GB DDR4 SODIMM`).

**Required:** Yes.

### Notes

Optional notes about this hardware component.

### Part Number

SKU or manufacturer part number

### Slug

URL-safe name representation.

**Required:** Yes.

### Specs

JSON dictionary storing specific technical properties (e.g. speed, latency).

### Supplier

Supplier associated with this hardware component.

### Tenant

Tenant that owns or scopes this hardware component.


## Stock & Allocation Lifecycle
Components reside in local stock repositories before being physically installed into parent servers or workstations. Installing components registers an allocation, deducting quantities from the warehouse stock automatically.
