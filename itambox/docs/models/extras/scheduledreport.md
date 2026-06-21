# Scheduled Reports

A **Scheduled Report** configures periodic background compilation of a Report Template using cron cadences and delivers the results via email or notification channels.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Channels** | delivery channels (`NotificationChannel`). | Many to Many | No |
| **Cron Expression** | Cron string evaluated via `croniter` (e.g. `0 9 * * 1`). | String | Yes |
| **Filter Tenants** | Many-to-many list of tenants to scope data (constellation filters). | Many to Many | No |
| **Format** | Delivery layout format: `html`, `csv`. | Choice | Yes |
| **Frequency** | The frequency of the scheduled report. | Choice | Yes |
| **Is Active** | Active schedules are evaluated by workers. | Boolean | Yes |
| **Last Run** | Timestamp of last execution. | DateTime | No |
| **Last Status** | Execution outcome summary (`success` or `failed`). | String | No |
| **Name** | Display name of the scheduled job. | String | Yes |
| **Recipients** | Comma-separated list of target email addresses. | Text | Yes |
| **Report** | The Report Template to compile. | Foreign Key | Yes |
| **Save To Archive** | Toggles whether output is saved to the archive database. | Boolean | Yes |
| **Schedule** | Linked Django-Q Schedule tracking next run. | Foreign Key | No |
| **Start Time** | Time of day to run the schedule (e.g. 08:00:00) | TimeField | No |
| **Tenant** | The tenant owning this scheduled report. Null represents system-wide schedules. | Foreign Key | No |

## Workflow & Cleanup

* **Email Validation**: Scans and parses `recipients` strings to validate format syntax.
* **Auto-Cleanup**: A `post_delete` signal deletes the linked `django-q2` task schedule when the `ScheduledReport` is deleted to prevent orphaned tasks.
