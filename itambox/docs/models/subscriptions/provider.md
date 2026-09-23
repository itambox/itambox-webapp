# SaaS Providers

A **SaaS Provider** represents a cloud platform hosting provider, software vendor, or web application developer offering subscription services (e.g. `Figma`, `AWS`, `Salesforce`, `Atlassian`). A Provider can be linked to a shared [Supplier](../assets/supplier.md) so both records reuse one vendor identity; see [Commercial Vocabulary](../../usage/commercial-vocabulary.md).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Account ID** | Customer account number with this provider. | String | No |
| **Admin Notes** | Internal administrative notes. | Text | No |
| **Contacts** | Assigned contacts from the unified organization contact system. | GenericRelation | No |
| **Active** | Toggle to show/hide this provider in selections. | Boolean | Yes |
| **Name** | Unique name of the SaaS provider (e.g., Adobe Inc.). May be derived from the linked Supplier when left blank. | String | Yes |
| **Admin Portal URL** | Administration or configuration management console portal link. | URL | No |
| **Slug** | URL-friendly identifier (auto-generated if blank). | Slug | Yes |
| **Supplier** | Optional link to the shared Supplier record for this vendor. | Foreign Key | No |
| **Tenant** | Optional tenant scoping this provider. Null represents system-wide/global. | ForeignKey | No |
| **Tenant Group** | Optional tenant group scoping this provider. | ForeignKey | No |
