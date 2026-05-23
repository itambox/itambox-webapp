# Tenant Roles

A **Tenant Role** represents a tenant-specific permission role. It allows tenant administrators to define custom levels of access (e.g. *Read-Only Auditor*, *Asset Manager*, *Hardware Stager*) within their tenant's isolation boundaries, without needing global Django admin access.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Tenant** | The Tenant context this role is bound to. | Foreign Key | Yes |
| **Name** | The name of the role (e.g., `Auditor`). | String | Yes |
| **Description** | Optional notes detailing the purpose of the role. | Text | No |
| **Permissions** | A list of string permission keys granted to members of this role (e.g., `["view_asset", "add_assetrequest"]`). | JSON | No |

---

## Business Logic
Tenant Roles enforce granular, tenant-scoped access control (RBAC). All permissions listed in the `permissions` field are checked at the API and GraphQL layers against the active user's active tenant membership.
