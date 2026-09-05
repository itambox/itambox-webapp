# Tenant Memberships

A **Tenant Membership** links a standard User account to a specific Tenant and assigns them a Tenant Role. This model is the core bridge for enforcing multi-tenant isolation, granting users the appropriate permissions to view or edit resources owned by that specific Tenant.

---

## Fields

### Joined At

The timestamp when the membership was created.

**Required:** Yes (Auto).

### Role

The Tenant Role mapping permissions to the user.

**Required:** Yes.

### Tenant

The Tenant the user is becoming a member of.

**Required:** Yes.

### User

The User account being granted access.

**Required:** Yes.


## Multi-Tenancy Scope
A user can hold multiple memberships across different tenants. When making API or GraphQL queries, the system scopes the active data visibility and allowed operations based on the tenant context specified in the query/session header and the corresponding user's `TenantMembership` role permissions.
