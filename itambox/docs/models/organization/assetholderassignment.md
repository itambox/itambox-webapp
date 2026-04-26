# Asset Holder Assignments

An **Asset Holder Assignment** is a generic mapping relationship linking an `Asset Holder` to other polymorphic entities in the database (such as roles, departments, or custom groups).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Holder** | The parent asset holder profile being assigned. | Foreign Key | Yes |
| **Content Type** | The target database table being mapped (using Django ContentTypes). | Foreign Key | Yes |
| **Object ID** | The unique primary key of the target object in the selected table. | Integer | Yes |
