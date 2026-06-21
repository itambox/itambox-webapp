# Alert Logs

An **Alert Log** registers individual instances of active `AlertRule` violations, tracking the resolution process from active alert to resolution.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Acknowledged By** | User who acknowledged the alert. | Foreign Key | No |
| **Content Type** | Polymorphic link to target object (Django ContentType). | Foreign Key | Yes |
| **Delivery Status** | Per-channel delivery outcomes dictionary. | JSON | Yes |
| **Last Notified At** | Timestamp of last dispatched notification. | DateTime | No |
| **Message** | Explanatory message details. | Text | Yes |
| **Object ID** | Unique primary key of target object. | Integer | Yes |
| **Resolution Notes** | Description of corrective action taken. | Text | No |
| **Resolved At** | Timestamp of resolution. | DateTime | No |
| **Resolved By** | User who resolved or closed the alert. | Foreign Key | No |
| **Rule** | The parent Alert Rule. | Foreign Key | Yes |
| **Severity** | Copied from the rule: `info`, `warning`, `critical`. | Choice | Yes |
| **Status** | Resolution state: `active`, `acknowledged`, `resolved`. | Choice | Yes |
| **Subject** | Brief subject summary of the alert. | String | Yes |
| **Tenant** | Scopes target queries and ownership. | Foreign Key | No |

## Lifecycle

* **Deduplication**: The database enforces a partial unique constraint preventing duplicate active logs for the same rule/object combination.
* **Auto-Resolution**: Active alert logs are automatically resolved when conditions clear (e.g. stock replenishment).
* **Cascade Safeguard**: Uses cross-tenant unscoped managers to safely resolve soft-deleted target objects in UI history logs.
