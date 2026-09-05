# Notification Channels

A **Notification Channel** defines a destination or medium for alert rules and system notifications, such as SMTP (email) or custom webhook endpoints.

## Fields

### Channel Type

The type of delivery channel (e.g., SMTP, Webhook, Slack).

**Required:** Yes.

### Config

Channel-specific configuration payload (SMTP settings, webhook URLs, authentication tokens, etc.).

### Enabled

Flag indicating if this channel is active and accepting notifications.

**Required:** Yes.

### Name

Unique user-friendly name for the notification channel.

**Required:** Yes.

### Tenant

The tenant owning this channel. Null represents a system-wide channel.


## Features & Validation

* **Multi-Channel Dispatch**: Supports sending system alerts through multiple communication methods.
* **Tenant Isolation**: System-wide channels can be utilized by all tenants, while tenant-specific channels are isolated to their respective owners.
