# Label Templates

A **Label Template** defines the printable layout and dimensions for physical asset tags, barcodes, or QR code labels.

## Fields

### Barcode Format

The barcode symbology used when the template renders a barcode.

**Required:** Yes.

### Description

Optional notes on label format and printer compatibility.

### Name

Human-readable name for this record.

**Required:** Yes.

### Page Height

Printable label height in inches.

**Required:** Yes.

### Page Width

Printable label width in inches.

**Required:** Yes.

### Template Code

Jinja2/HTML code specifying the graphical layout of the label.


## Features & Validation

* **Print Preview**: Generates print sheets scaled to label dimensions.
* **Dynamic Content**: Injects asset details like tag sequence numbers, categories, and serial numbers directly into the barcode layout.

Custom label code is a Beta print-layout feature. Only superusers can create or edit global templates. Rendering is sandboxed and sanitized before PDF generation.
