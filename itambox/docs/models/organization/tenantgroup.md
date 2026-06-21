# Tenant Groups

A **Tenant Group** organizes tenants into logical hierarchies (e.g. `Subsidiaries` -> `Regional Branches` or `Engineering` -> `DevOps`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Description** | Optional notes. | Text | No |
| **Name** | Unique name of the tenant group (e.g., `Engineering Departments`). | String | Yes |
| **Parent** | Hierarchical parent group. | Foreign Key | No |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Use Cases
Tenant Groups simplify global reporting, allowing administrators to audit costs and inventory allocations at the departmental parent tier (e.g. totaling software licensing costs for all of `Engineering`).
