# Alert Rules

An **Alert Rule** defines threshold limits or time-horizon conditions that trigger automated notifications when violated (e.g. low stock, upcoming contract expiration).

## Fields

### Alert Type

Trigger condition: `low_stock`, `upcoming_eol`, `license_expiry`, `renewal_due`, `warranty_expiry`, `audit_overdue`.

**Required:** Yes.

### Channels

Associated communication outlets (`NotificationChannel`).

### Description

Optional details.

### Is Active

Active rules are evaluated daily by background workers.

**Required:** Yes.

### Is Muted

If muted, alerts are logged to the dashboard but send no notifications.

**Required:** Yes.

### Last Fired At

Timestamp of last evaluation run.

### Name

Display name of the alert rule.

**Required:** Yes.

### Renotify Interval Days

Days between repeat notifications. `0` disables repetition.

**Required:** Yes.

### Severity

Rule severity: `info`, `warning`, `critical`.

**Required:** Yes.

### Tenant

Scopes target queries and ownership.

### Threshold Value

Numeric value (e.g. unit count or days horizon).

**Required:** Yes.
