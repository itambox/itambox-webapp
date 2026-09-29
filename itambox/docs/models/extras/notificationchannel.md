# Notification Channels

A **Notification Channel** defines a destination or medium for alert rules and system notifications, such as SMTP (email) or custom webhook endpoints.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Channel Type** | The type of delivery channel (e.g., SMTP, Webhook, Slack). | Choice | Yes |
| **Config** | Channel-specific configuration payload (SMTP settings, webhook URLs, authentication tokens, etc.). | JSON | No |
| **Enabled** | Flag indicating if this channel is active and accepting notifications. Disabled channels are never contacted by rule dispatch. | Boolean | Yes |
| **Name** | Unique user-friendly name for the notification channel. | String | Yes |
| **Tenant** | The tenant owning this channel. Null represents a system-wide channel. | Foreign Key | No |

## Features & Validation

* **Multi-Channel Dispatch**: Supports sending system alerts through multiple communication methods.
* **Channel Scope**: A rule delivers only through channels of its own scope — a tenant-scoped rule through its own tenant's channels, a platform-wide rule through platform-wide channels. Out-of-scope attachments are rejected at the form and API boundaries and are never dispatched.
* **Enabled Gate**: A disabled channel is never contacted; if every attached channel is disabled, the alert records an explicit reason instead of a silent success.
* **Tenant Isolation**: Tenant-specific channels are isolated to their respective owners, and deliveries never cross tenant boundaries.
