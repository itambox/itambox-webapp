# Custom Fieldsets

A **Custom Fieldset** groups custom fields together into logical sections on the user interface, improving form organization and layout consistency.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Custom Fields** | The list of custom fields included in this fieldset. | Many-to-Many | No |
| **Fieldset Name** | The header or section name displayed in user forms. | String | Yes |

## Features & Validation

* **Explicit Composition**: Asset Types select an ordered list of Fieldsets. Member target bindings determine applicability, not automatic selection.
* **History Preservation**: Removing a Fieldset from a Type does not erase values that become historical.
* **Layout Organization**: Fields inside a fieldset are rendered together in the UI, enhancing readability for large sets of custom properties.
