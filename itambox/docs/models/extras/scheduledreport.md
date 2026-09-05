# Scheduled Reports

A **Scheduled Report** configures periodic background compilation of a Report Template using cron cadences and delivers the results via email or notification channels.

## Fields

### Channels

delivery channels (`NotificationChannel`).

### Cron Expression

Cron string evaluated via `croniter` (e.g. `0 9 * * 1`).

**Required:** Yes.

### Filter Tenants

Many-to-many list of tenants to scope data (constellation filters).

### Format

Delivery layout format: `html` (inline email), `csv`, `pdf` (rendered via xhtml2pdf), or `xlsx` (Excel via openpyxl). CSV/PDF/XLSX are delivered as attachments.

**Required:** Yes.

### Frequency

Schedule frequency for this report.

**Required:** Yes.

### Is Active

Active schedules are evaluated by workers.

**Required:** Yes.

### Last Run

Timestamp of last execution.

### Last Status

Execution outcome summary (`success` or `failed`).

### Name

Display name of the scheduled job.

**Required:** Yes.

### Recipients

Comma-separated list of target email addresses.

**Required:** Yes.

### Report

The Report Template to compile.

**Required:** Yes.

### Save To Archive

Toggles whether output is saved to the archive database.

**Required:** Yes.

### Schedule

The schedule information used to track the next run.

### Start Time

Time of day to run the schedule (e.g. 08:00:00)

### Tenant

The tenant owning this scheduled report. Null represents system-wide schedules.


## Workflow & Cleanup

* **Email Validation**: Scans and parses `recipients` strings to validate format syntax.
* **Schedule cleanup**: Removing a scheduled report also removes the schedule used for its future runs.
