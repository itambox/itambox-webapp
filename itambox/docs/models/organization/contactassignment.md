# Contact Assignments

A **Contact Assignment** maps a `Contact` to a target database object (such as a Manufacturer, Tenant, Supplier, or Site) under a specific `Contact Role`. This enables polymorphic contact directories.

## Fields

### Assigned Object

The ITAMbox record to which this contact is assigned.

**Required:** Yes.

### Contact

The individual or entity being assigned.

**Required:** Yes.

### Content Type

The target database table being assigned (e.g. `Manufacturer`).

**Required:** Yes.

### Object ID

The identifier of the record receiving the contact assignment.

**Required:** Yes.

### Priority

Priority level: `Primary`, `Secondary`, `Tertiary`, `Inactive`.

### Role

The contact role used for this assignment.

**Required:** Yes.
