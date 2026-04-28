# SaaS Providers

A **SaaS Provider** represents a cloud platform hosting provider, software vendor, or web application developer offering subscription services (e.g. `Figma`, `AWS`, `Salesforce`, `Atlassian`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the SaaS provider. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Homepage** | SaaS provider portal link. | URL | No |
| **Support Phone** | Primary support telephone number. | String | No |
| **Support Email** | Direct account rep or support email address. | Email | No |
| **Description** | Optional notes detailing contract SLA tiers or SSO login setup pages. | Text | No |
