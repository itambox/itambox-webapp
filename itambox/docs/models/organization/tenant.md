# Tenants

A **Tenant** represents an internal department, business unit, client, or subsidiary that owns, funds, or occupies specific assets or sites. It serves as the primary mechanism for financial cost allocation and data isolation boundaries.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the tenant (e.g., `Finance Department`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Group** | The parent tenant group classification. | Foreign Key | No |
| **Description** | Optional descriptive details. | Text | No |

## Use Cases
Tenancy maps assets, software licenses, SaaS subscriptions, and inventory items directly to specific cost centers for budgeting, billing, and accounting reconciliations.
