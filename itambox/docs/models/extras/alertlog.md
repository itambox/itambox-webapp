# Alert Logs

An **Alert Log** registers individual instances of active `AlertRule` violations, tracking the resolution process from active alert to resolution.

## Fields

### Acknowledged By

User who acknowledged the alert.

### Content Type

The ITAMbox object that triggered this alert.

**Required:** Yes.

### Delivery Status

Per-channel delivery outcomes dictionary.

**Required:** Yes.

### Last Notified At

Timestamp of last dispatched notification.

### Message

Explanatory message details.

**Required:** Yes.

### Object ID

Unique primary key of target object.

**Required:** Yes.

### Resolution Notes

Description of corrective action taken.

### Resolved At

Timestamp of resolution.

### Resolved By

User who resolved or closed the alert.

### Rule

The parent Alert Rule.

**Required:** Yes.

### Severity

Copied from the rule: `info`, `warning`, `critical`.

**Required:** Yes.

### Status

Resolution state: `active`, `acknowledged`, `resolved`.

**Required:** Yes.

### Subject

Brief subject summary of the alert.

**Required:** Yes.

### Tenant

Scopes target queries and ownership.


## Lifecycle

* **Deduplication**: The database enforces a partial unique constraint preventing duplicate active logs for the same rule/object combination.
* **Auto-Resolution**: Active alert logs are automatically resolved when conditions clear (e.g. stock replenishment).
* **History retention**: Alert history remains readable when the original target is no longer available.
