# Object Changelog

An **Object Change** records a user-visible change to an ITAMbox object. Changelog entries make it possible to review what changed, who performed the change, and when it happened.

For retention and operator commands, see [Data Retention](../../operations/data-retention.md) and [Management Commands](../../operations/management-commands.md).

## Fields

### Action

The kind of change recorded, such as Created, Modified, or Deleted.

**Required:** Yes.

### Changed Object

The ITAMbox object affected by the change.

### Changed Object ID

The recorded identifier of the changed object.

### Changed Object Type

The object type recorded for the changed item.

### Object Representation

A readable snapshot of how the changed object was identified at the time of the change.

### Object Type Representation

A readable name for the changed object's type.

### Prechange Data

The recorded field values before the change, when available.

### Postchange Data

The recorded field values after the change, when available.

### Related Object

An associated object recorded with the change when the operation involves another record.

### Request ID

An identifier that groups changes produced by the same request or operation.

### Tenant

The tenant that owns or scopes the changelog entry.

### Time

When the change was recorded.

**Required:** Yes.

### User

The user who performed the change, when that user record is still available.

### User Name

A stored username used to keep the actor recognizable if the original user account is later removed.

## Retention

Changelog retention is configurable globally and can be overridden per tenant. A tenant value of `0` retains its changelog indefinitely. System-wide entries follow the global retention policy.

Use the Administration documentation for pruning, preview, tenant scoping, and archival options rather than treating the Data Model page as an operator runbook.
