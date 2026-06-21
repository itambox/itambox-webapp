# Custom Fields

A **Custom Field** allows organizations to dynamically extend core models (such as Assets, Asset Types, and Tenants) with user-defined attributes without altering the database schema.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Choices** | Newline-separated list of values (only applicable if Type is Selection list). | Text | No |
| **Field Type** | Data type of the field (e.g., Text, Integer, Date, Boolean, Selection list). | Choice | Yes |
| **Display Label** | User-friendly label shown in forms and list headers. | String | Yes |
| **Field Name** | Slug-like database identifier (e.g., `sim_card_number`). | Slug | Yes |
| **Object Types** | The Django models this field applies to. | Many-to-Many | No |
| **Required** | If checked, validation requires a value to be set. | Boolean | Yes |

## Features & Validation

* **Target Binding**: A custom field can target specific object types (e.g., binding to *Asset Type* defines hardware specifications; binding to *Asset* represents per-device details).
* **Validation**: Respects field types and requirement flags when saving object instances.
