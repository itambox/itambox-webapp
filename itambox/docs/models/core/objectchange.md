# Object Changelog

An **Object Change** record represents an immutable, comprehensive system audit trail capturing every creation, update, soft-delete, or recovery action executed on objects across the entire application.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Action** | Type of transaction: `Created`, `Modified`, `Deleted`. | Choice | Yes |
| **Changed Object** | Polymorphic pointer to the specific object database row using a Generic Foreign Key. | GFK | Yes |
| **Changed Object Id** | The changed object id of the object change. | Integer | Yes |
| **Changed Object Type** | The changed object type of the object change. | Foreign Key | Yes |
| **Object Repr** | The object repr of the object change. | String | Yes |
| **Object Type Repr** | The object type repr of the object change. | String | No |
| **Postchange Data** | The postchange data of the object change. | JSON | No |
| **Prechange Data** | The prechange data of the object change. | JSON | No |
| **Related Object** | The related object of the object change. | GenericForeignKey | Yes |
| **Related Object Id** | The related object id of the object change. | Integer | No |
| **Related Object Type** | The related object type of the object change. | Foreign Key | No |
| **Request ID** | A UUID tying multiple model changes back to a single HTTP request context. | UUID | Yes |
| **Tenant** | The tenant of the object change. | Foreign Key | No |
| **Time** | Precise timestamp of the transaction commit. | DateTime | Yes |
| **User** | The Django User who executed the change. | Foreign Key | No |
| **User Name** | Flat username backup (useful if the User account is later deleted). | String | Yes |

## Audit Integrity
ITAMbox generates changelogs asynchronously or at the database transaction layer using model lifecycle signals. Every detail view includes a "Changelog" tab displaying a clean diff table detailing exactly what fields changed, who did it, and when.
