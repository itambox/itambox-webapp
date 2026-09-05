# Tenants

A **Tenant** represents an ownership and access scope for tenant-aware data, such as an MSP customer or an internal business unit. It is the primary tenant boundary; Cost Centers provide separate financial allocation.

## Fields

### Changelog Retention Days

Per-tenant override of `ITAMBOX_CHANGELOG_RETENTION_DAYS`. Blank follows global setting, `0` = legal hold (never prune).

### Comments

Additional comments about this tenant.

### Currency

ISO 4217 currency code used for value display (display only, no conversion).

**Required:** Yes.

### Default Depreciation

Fallback policy applied to all assets that have no type-level schedule and no per-asset override.

### Description

Optional descriptive details.

### Group

The parent tenant group classification.

### Is Provider

Marks this tenant as a service provider that can manage other tenants.

**Required:** Yes.

### Managed By

Parent provider tenant that manages this tenant (for MSP hierarchies).

### Name

Unique name of the tenant (e.g. `Finance Department`).

**Required:** Yes.

### Slug

URL-safe name representation.

**Required:** Yes.


## Use Cases
Tenancy maps assets, software licenses, SaaS subscriptions, and inventory items directly to specific cost centers for budgeting, billing, and accounting reconciliations.
