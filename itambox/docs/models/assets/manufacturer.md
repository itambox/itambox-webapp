# Manufacturers

A **Manufacturer** represents a hardware vendor or software developer producing items tracked in your inventory (e.g. `Dell`, `Lenovo`, `Apple`, `Cisco`, `Microsoft`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Contacts** | The contacts of the manufacturer. | Many-to-Many | No |
| **Description** | Optional notes detailing primary vendor contacts or warranty links. | Text | No |
| **Name** | Unique name of the manufacturer. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Support Contacts
Manufacturers support a polymorphic generic relation to `Contact Assignment`. ITAMbox can automatically resolve the active support contact, first searching for contacts with roles matching `support` or `technical-support`, falling back to `primary` contacts, and eventually any registered vendor assignments.
