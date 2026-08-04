# Label Templates

A **Label Template** defines the printable layout and dimensions for physical asset tags, barcodes, or QR code labels.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Barcode Format** | The format of the barcode or QR code (e.g., Code 128, QR Code). | String | Yes |
| **Description** | Optional notes on label format and printer compatibility. | Text | No |
| **Name** | The name of the label layout. | String | Yes |
| **Page Height** | Printable label height in inches. | Float | Yes |
| **Page Width** | Printable label width in inches. | Float | Yes |
| **Template Code** | Jinja2/HTML code specifying the graphical layout of the label. | Text | No |

## Features & Validation

* **Print Preview**: Generates print sheets scaled to label dimensions.
* **Dynamic Content**: Injects asset details like tag sequence numbers, categories, and serial numbers directly into the barcode layout.

Custom label code is a Beta print-layout feature. Only superusers can create or
edit the global templates; rendering exposes a scalar asset DTO rather than the
Django model, uses an immutable autoescaping Jinja sandbox, and sanitizes the
resulting HTML/CSS/resources before PDF generation.
