# Software Catalog

The **Software Catalog** tracks software applications, operating systems, and developer tools approved for use inside your organization (e.g. `Microsoft Office 365`, `Windows 11 Enterprise`, `Adobe Creative Cloud`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the software application. | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Manufacturer** | Software developer (e.g. `Microsoft`). | Foreign Key | Yes |
| **Category** | Optional classification category. | Foreign Key | No |
| **Notes** | Description or deployment instructions. | Text | No |

## Use Cases
The Software Catalog maps catalog definitions of software titles. It is distinct from individual licenses or subscriptions, serving as a template under which multiple separate licenses (seats) or SaaS subscriptions can be purchased and assigned.
