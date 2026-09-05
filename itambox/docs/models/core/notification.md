# Notifications

A **Notification** represents an in-app alert delivered to a specific user or broadcast globally. Notifications carry a subject, message body, severity level, and an optional click-through target URL. They support read/unread tracking and are ordered by recency.

---

## Fields

### User

The target user for the notification. A null user represents a global broadcast alert visible to all users.

### Subject

Short title or heading for the notification.

**Required:** Yes.

### Message

Full body text of the notification.

**Required:** Yes.

### Level

Severity / visual style: `Info`, `Warning`, `Success`, or `Danger`.

**Required:** Yes.

### Is Read

Whether the target user has marked the notification as read. Defaults to `False`.

**Required:** Yes.

### Target URL

Optional destination URL opened when the notification is clicked.

### Created At

Timestamp when the notification was generated (auto-set).

**Required:** Yes.


---

## Usage Patterns

- **User-scoped**: When `user` is set, the notification is delivered to that specific user's inbox.
- **Global broadcast**: When `user` is null, the notification is visible to all users (e.g. system maintenance announcements).
- **Read tracking**: The `is_read` flag and the composite index on `(user, is_read)` enable efficient querying of unread notifications per user.
- **Click-through**: The optional `target_url` provides deep-linking: clicking the notification can navigate directly to the relevant object or page.
