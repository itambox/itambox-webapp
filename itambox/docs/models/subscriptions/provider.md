# SaaS Providers

A **SaaS Provider** represents a cloud platform hosting provider, software vendor, or web application developer offering subscription services (e.g. `Figma`, `AWS`, `Salesforce`, `Atlassian`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the SaaS provider (e.g., Adobe Inc.). | String | Yes |
| **Slug** | URL-friendly identifier. | Slug | Yes |
| **Account ID** | customer account number with this provider. | String | No |
| **Admin Portal URL**| Administration or configuration management console portal link. | URL | No |
| **Company Website**| Public website address of the provider. | URL | No |
| **Contact Email** | Primary support or account rep email address. | Email | No |
| **Contact Phone** | Primary support or billing telephone number. | String | No |
| **Support Details**| Escalation paths, contact hours, or support ticket URLs. | Text | No |
| **Admin Notes** | Internal administrative notes. | Text | No |
| **Active** | Toggle to show/hide this provider in selections. | Boolean | Yes |
| **Description** | Optional notes detailing contract SLAs or SSO integrations. | Text | No |
