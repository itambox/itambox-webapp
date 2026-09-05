# Manufacturers

A **Manufacturer** represents a hardware vendor or software developer producing items tracked in your inventory (e.g. `Dell`, `Lenovo`, `Apple`, `Cisco`, `Microsoft`).

## Fields

### Contacts

Contacts associated with this record.

### Description

Optional notes detailing primary vendor contacts or warranty links.

### Name

Unique name of the manufacturer.

**Required:** Yes.

### Slug

URL-safe name representation.

**Required:** Yes.


## Support Contacts
Manufacturers support a polymorphic generic relation to `Contact Assignment`. ITAMbox can automatically resolve the active support contact, first searching for contacts with roles matching `support` or `technical-support`, falling back to `primary` contacts, and eventually any registered vendor assignments.
