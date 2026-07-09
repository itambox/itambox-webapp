# Alert Rules

An **Alert Rule** defines threshold limits or time-horizon conditions that trigger automated notifications when violated (e.g. low stock, upcoming contract expiration).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Alert Type** | Trigger condition: `low_stock`, `upcoming_eol`, `license_expiry`, `renewal_due`, `warranty_expiry`, `audit_overdue`. | Choice | Yes |
| **Channels** | Associated communication outlets (`NotificationChannel`). | Many to Many | No |
| **Description** | Optional details. | Text | No |
| **Is Active** | Active rules are evaluated daily by background workers. | Boolean | Yes |
| **Is Muted** | If muted, alerts are logged to the dashboard but send no notifications. | Boolean | Yes |
| **Last Fired At** | Timestamp of last evaluation run. | DateTime | No |
| **Name** | Display name of the alert rule. | String | Yes |
| **Renotify Interval Days** | Days between repeat notifications. `0` disables repetition. | Integer | Yes |
| **Severity** | Rule severity: `info`, `warning`, `critical`. | Choice | Yes |
| **Tenant** | Scopes target queries and ownership. | Foreign Key | No |
| **Threshold Value** | Numeric value (e.g. unit count or days horizon). | Integer | Yes |

