# Tenants

A **Tenant** represents an internal department, business unit, client, or subsidiary that owns, funds, or occupies specific assets or sites. It serves as the primary mechanism for financial cost allocation and data isolation boundaries.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Changelog Retention Days** | Per-tenant override of `ITAMBOX_CHANGELOG_RETENTION_DAYS`. Blank follows global setting, `0` = legal hold (never prune). | Integer | No |
| **Comments** | The comments of the tenant. | Text | No |
| **Currency** | ISO 4217 currency code used for value display (display only, no conversion). | String | Yes |
| **Default Depreciation** | Fallback policy applied to all assets that have no type-level schedule and no per-asset override. | Foreign Key | No |
| **Description** | Optional descriptive details. | Text | No |
| **Group** | The parent tenant group classification. | Foreign Key | No |
| **Is Provider** | Marks this tenant as a service provider that can manage other tenants. | Boolean | Yes |
| **Managed By** | Parent provider tenant that manages this tenant (for MSP hierarchies). | Foreign Key | No |
| **Name** | Unique name of the tenant (e.g. `Finance Department`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Use Cases
Tenancy maps assets, software licenses, SaaS subscriptions, and inventory items directly to specific cost centers for budgeting, billing, and accounting reconciliations.
