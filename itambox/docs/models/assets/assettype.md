# Asset Types

An **Asset Type** represents a specific model of hardware manufactured by a vendor (e.g. `Dell Latitude 7440`, `Apple MacBook Pro 16-inch M3`, `Cisco Catalyst 9300`). It defines the baseline specifications, EOL limits, depreciation configurations, and custom metadata for all physical assets of this type.

## Fields

### Asset Role

The default operational role associated with assets of this type.

### Category

The asset category containing checkout rules.

### Comments

Additional comments about this asset type.

### Custom Fieldset

Associated custom fields grouping.

### Depreciation

Straight-line depreciation rule template to apply.

### Description

Human-readable description of this asset type.

### Ean

Barcode (EAN / UPC / GTIN): scanning shows assets of this type.

### EOL (Months)

Useful lifespan in months before End-of-Life replacement is due.

### Model Image

Product image for this asset type model.

### Manufacturer

The company that manufactured the hardware (e.g., `Apple`).

**Required:** Yes.

### Model

The specific model name or number (e.g., `MacBook Pro 16"`).

**Required:** Yes.

### Part Number

Manufacturer part number or SKU

### Requestable

Allows end-users to request assets of this type.

**Required:** Yes.

### Slug

Auto-slug source concatenating Manufacturer + Model.

**Required:** Yes.


## Speclist Inheritance
All physical assets inherit their base hardware specifications (RAM, CPU, Storage) from their defined **Asset Type**, eliminating redundant field editing across identical systems.
