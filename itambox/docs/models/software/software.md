# Software Catalog

The **Software Catalog** tracks software applications, operating systems, and developer tools approved for use inside your organization (e.g. `Microsoft Office 365`, `Windows 11 Enterprise`, `Adobe Creative Cloud`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Category** | Optional classification category. | Foreign Key | No |
| **Description** | Optional description of the software product. | Text | No |
| **License Type** | Default license type | Choice | No |
| **Manufacturer** | Software developer (e.g. `Microsoft`). | Foreign Key | Yes |
| **Name** | Unique name of the software application. | String | Yes |
| **Tenant** | Owning tenant. Null denotes a shared/global catalogue entry visible to all tenants. | Foreign Key | No |
| **Version** | Current version (e.g., 2021, 16.0) | String | No |
| **Website** | Product homepage or vendor URL | String | No |

## Use Cases
The Software Catalog maps catalog definitions of software titles. It is distinct from individual licenses or subscriptions, serving as a template under which multiple separate licenses (seats) or SaaS subscriptions can be purchased and assigned.
