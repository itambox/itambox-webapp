# Custom Fieldsets

A **Custom Fieldset** groups custom fields together into logical sections on the user interface, improving form organization and layout consistency.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Custom Fields** | The list of custom fields included in this fieldset. | Many-to-Many | No |
| **Fieldset Name** | The header or section name displayed in user forms. | String | Yes |

## Features & Validation

* **Form Grouping**: Custom fieldsets automatically structure the target model edit forms into distinct panels.
* **Layout Organization**: Fields inside a fieldset are rendered together in the UI, enhancing readability for large sets of custom properties.
