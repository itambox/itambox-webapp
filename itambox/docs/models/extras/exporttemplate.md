# Export Templates

An **Export Template** defines a custom formatting template (using Jinja2 or Django template syntax) used to export lists of objects into custom formats like CSV, XML, JSON, or custom text structures.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Content Type** | The model type that this export template applies to. | Foreign Key | Yes |
| **Description** | Optional notes describing what format this template outputs. | Text | No |
| **File Extension** | The default file extension for the download (e.g. `.csv`, `.json`). | String | Yes |
| **MIME Type** | The MIME type sent in the HTTP response headers (e.g. `text/csv`, `application/json`). | String | Yes |
| **Name** | A unique name identifying the export template. | String | Yes |
| **Template Code** | Django or Jinja2 template content defining the export layout. | Text | Yes |

## Features & Validation

* **Templated Rendering**: Safely renders a list of objects through the template engine.
* **Flexible MIME types**: Allows formatting output as spreadsheets, config files, or custom scripts.
