# Tenant Roles

A **Tenant Role** represents a tenant-specific permission role. Tenant administrators can use roles such as *Read-Only Auditor*, *Asset Manager*, or *Hardware Stager* to grant appropriate access within a tenant.

---

## Fields

### Description

Optional notes detailing the purpose of the role.

### Name

Human-readable name for this record.

**Required:** Yes.

### Permissions

A list of string permission keys granted to members of this role (e.g., `["view_asset", "add_assetrequest"]`).

### Tenant

The Tenant context this role is bound to.

**Required:** Yes.


## Business Logic
Tenant Roles enforce granular, tenant-scoped access control (RBAC). All permissions listed in the `permissions` field are checked at the API and GraphQL layers against the active user's active tenant membership.
