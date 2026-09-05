# Export Templates

An **Export Template** defines a Jinja2 layout used to render a result set into a custom download format such as CSV, JSON, XML, or plain text.

## Fields

### Content Type

The model type that this export template applies to.

**Required:** Yes.

### Description

Optional notes describing what format this template outputs.

### File Extension

The default file extension for the download (e.g. `.csv`, `.json`).

**Required:** Yes.

### Download As Attachment

Controls whether the rendered output is downloaded as a file or displayed inline in the browser.

### MIME Type

The MIME type sent in the HTTP response headers (e.g. `text/csv`, `application/json`).

**Required:** Yes.

### Name

A unique name identifying the export template.

**Required:** Yes.

### Template Code

Jinja2 template content that defines the exported output. The selected result set is available to the template as `queryset`.

**Required:** Yes.


## Features & Validation

* **Templated Rendering**: Safely renders a list of objects through the template engine.
* **Flexible MIME types**: Allows formatting output as spreadsheets, config files, or custom scripts.
