# Object Changelog

An **Object Change** record represents an immutable, comprehensive system audit trail capturing every creation, update, soft-delete, or recovery action executed on objects across the entire application.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Time** | Precise timestamp of the transaction commit. | DateTime | Yes |
| **User** | The Django User who executed the change. | Foreign Key | No |
| **User Name** | Flat username backup (useful if the User account is later deleted). | String | Yes |
| **Request ID** | A UUID tying multiple model changes back to a single HTTP request context. | UUID | Yes |
| **Action** | Type of transaction: `Created`, `Modified`, `Deleted`. | Choice | Yes |
| **Changed Object** | Polymorphic pointer to the specific object database row using a Generic Foreign Key. | GFK | Yes |
| **Object Representation** | Flat string representation of the object seen during modification. | String | Yes |
| **Pre-change Data** | JSON snapshot containing database fields *prior* to saving. | JSON | No |
| **Post-change Data** | JSON snapshot containing database fields *after* saving. | JSON | No |

## Audit Integrity
AssetBox generates changelogs asynchronously or at the database transaction layer using model lifecycle signals. Every detail view includes a "Changelog" tab displaying a clean diff table detailing exactly what fields changed, who did it, and when.
