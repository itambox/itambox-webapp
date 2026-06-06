# SaaS Providers

A **SaaS Provider** represents a cloud platform hosting provider, software vendor, or web application developer offering subscription services (e.g. `Figma`, `AWS`, `Salesforce`, `Atlassian`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the SaaS provider (e.g., Adobe Inc.). | String | Yes |
| **Slug** | URL-friendly identifier (auto-generated if blank). | Slug | Yes |
| **Account ID** | Customer account number with this provider. | String | No |
| **Admin Portal URL**| Administration or configuration management console portal link. | URL | No |
| **Admin Notes** | Internal administrative notes. | Text | No |
| **Active** | Toggle to show/hide this provider in selections. | Boolean | Yes |
| **Tenant** | Optional tenant scoping this provider. Null represents system-wide/global. | ForeignKey | No |
| **Tenant Group**| Optional tenant group scoping this provider. | ForeignKey | No |
| **Contacts** | Assigned contacts from the unified organization contact system. | GenericRelation | No |
| **Tags** | Categorization labels. | Many-to-Many | No |
