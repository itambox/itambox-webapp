# Asset Holders

An **Asset Holder** represents a logical or physical recipient eligible to receive checked-out assets (such as systems, hardware, licenses, accessories, or consumables). This is typically an employee, a contractor, a service department, or a team.

## Fields

### Comments

Additional comments about this asset holder.

### Description

Human-readable description of this asset holder.

### Email

Contact email address.

### First Name

The holder's given name.

**Required:** Yes.

### Last Name

The holder's family name.

**Required:** Yes.

### Tenant

Optional department/tenant this holder is associated with for cost tracking.

### Upn

The holder's user principal name, when one is known.

**Required:** Yes.

### User

Optional link to an ITAMbox user account. A user may be linked to multiple Asset Holders.


## Property Properties

* **Checked out asset count**: Returns the quantity of active assets currently checked out to this holder.
* **Checked out assets list**: Returns all active `AssetAssignment` records mapped to this holder.
