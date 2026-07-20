# Contact Assignments

A **Contact Assignment** maps a `Contact` to a target database object (such as a Manufacturer, Tenant, Supplier, or Site) under a specific `Contact Role`. This enables polymorphic contact directories.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Assigned Object** | The assigned object of the contact assignment. | GenericForeignKey | Yes |
| **Contact** | The individual or entity being assigned. | Foreign Key | Yes |
| **Content Type** | The target database table being assigned (e.g. `Manufacturer`). | Foreign Key | Yes |
| **Object ID** | The unique primary key of the target object. | Integer | Yes |
| **Priority** | Priority level: `Primary`, `Secondary`, `Tertiary`, `Inactive`. | Choice | No |
| **Role** | The functional role of the contact for this assignment (e.g. `Emergency Contact`). | Foreign Key | Yes |
