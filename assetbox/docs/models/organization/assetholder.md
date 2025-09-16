# Asset Holders

An **Asset Holder** represents a logical or physical recipient eligible to receive checked-out assets (such as systems, hardware, licenses, accessories, or consumables). This is typically an employee, a contractor, a service department, or a team.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **User** | Optional one-to-one link to a Django user authentication profile. | OneToOne | No |
| **First Name** | The first name of the holder. | String | Yes |
| **Last Name** | The last name of the holder. | String | Yes |
| **User Principal Name (UPN)** | The unique organizational identifier, typically an Active Directory / LDAP UPN (e.g. `jane.doe@company.com`). | String | Yes |
| **Email** | Contact email address. | Email | No |
| **Tenant** | Optional department/tenant this holder is associated with for cost tracking. | Foreign Key | No |

## Property Properties

* **Checked out asset count**: Returns the quantity of active assets currently checked out to this holder.
* **Checked out assets list**: Returns all active `AssetAssignment` records mapped to this holder.
