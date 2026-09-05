# Cost Centers

A **Cost Center** represents a department or cost code structure within an organization, used to scope and track financial ownership for assets, licenses, subscriptions, and contracts.

Cost Centers are organized hierarchically: a top-level parent represents a primary cost center, and child sub-records represent departments inside it.

## Fields

### Code

Short identifier code (e.g. `CC-RD-100`).

**Required:** Yes.

### Description

Optional details explaining the cost center scope.

### Is Active

Boolean indicating if this cost center is active. Inactive centers are hidden.

**Required:** Yes.

### Name

Display name of the cost center or department (e.g. `Research & Development`).

**Required:** Yes.

### Parent

Optional parent link to another Cost Center, enabling hierarchical nesting.

### Slug

URL-safe representation.

**Required:** Yes.

### Tenant

Optional tenant this cost center is scoped to.


## Constraints

* **Unique Code**: The identifier `code` must be unique per active tenant.
* **Unique Slug**: The `slug` must be globally unique across active cost centers.

## Properties

* **Depth**: Returns the zero-based depth level in the hierarchy (e.g. `0` for top-level, `1` for direct child).
