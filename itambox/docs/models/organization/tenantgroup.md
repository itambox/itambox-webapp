# Tenant Groups

A **Tenant Group** organizes tenants into logical hierarchies (e.g. `Subsidiaries` -> `Regional Branches` or `Engineering` -> `DevOps`).

## Fields

### Description

Optional notes.

### Name

Unique name of the tenant group (e.g., `Engineering Departments`).

**Required:** Yes.

### Parent

Hierarchical parent group.

### Slug

URL-safe name representation.

**Required:** Yes.


## Use Cases
Tenant Groups simplify global reporting, allowing administrators to audit costs and inventory allocations at the departmental parent tier (e.g. totaling software licensing costs for all of `Engineering`).
