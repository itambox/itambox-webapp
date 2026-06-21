# Notification Channels

A **Notification Channel** defines a destination or medium for alert rules and system notifications, such as SMTP (email) or custom webhook endpoints.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Channel Type** | The type of delivery channel (e.g., SMTP, Webhook, Slack). | Choice | Yes |
| **Config** | Channel-specific configuration payload (SMTP settings, webhook URLs, authentication tokens, etc.). | JSON | No |
| **Enabled** | Flag indicating if this channel is active and accepting notifications. | Boolean | Yes |
| **Name** | Unique user-friendly name for the notification channel. | String | Yes |
| **Tenant** | The tenant owning this channel. Null represents a system-wide channel. | Foreign Key | No |

## Features & Validation

* **Multi-Channel Dispatch**: Supports sending system alerts through multiple communication methods.
* **Tenant Isolation**: System-wide channels can be utilized by all tenants, while tenant-specific channels are isolated to their respective owners.
