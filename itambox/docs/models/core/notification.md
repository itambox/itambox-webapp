# Notifications

A **Notification** represents an in-app alert delivered to a specific user or broadcast globally. Notifications carry a subject, message body, severity level, and an optional click-through target URL. They support read/unread tracking and are ordered by recency.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **User** | The target user for the notification. A null user represents a global broadcast alert visible to all users. | Foreign Key | No |
| **Subject** | Short title or heading for the notification. | String (255) | Yes |
| **Message** | Full body text of the notification. | Text | Yes |
| **Level** | Severity / visual style: `Info`, `Warning`, `Success`, or `Danger`. | Choice | Yes |
| **Is Read** | Whether the target user has marked the notification as read. Defaults to `False`. | Boolean | Yes |
| **Target URL** | Optional destination URL opened when the notification is clicked. | String (500) | No |
| **Created At** | Timestamp when the notification was generated (auto-set). | DateTime | Yes |

---

## Usage Patterns

- **User-scoped**: When `user` is set, the notification is delivered to that specific user's inbox.
- **Global broadcast**: When `user` is null, the notification is visible to all users (e.g. system maintenance announcements).
- **Read tracking**: The `is_read` flag and the composite index on `(user, is_read)` enable efficient querying of unread notifications per user.
- **Click-through**: The optional `target_url` provides deep-linking — clicking the notification can navigate directly to the relevant object or page.
