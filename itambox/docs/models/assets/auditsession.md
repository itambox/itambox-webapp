# Audit Sessions

An **Audit Session** represents a physical inventory reconciliation campaign scheduled to verify the existence, location accuracy, and operational health of assets.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | A clear name for the audit campaign (e.g. `Q2 IT Stockroom audit`). | String | Yes |
| **Location** | Optional Site Location room to focus the audit on. If blank, the audit campaign is global. | Foreign Key | No |
| **Status** | Session state: `Planned`, `Active`, `Completed`. | Choice | Yes |
| **Started At** | The timestamp when the audit was opened. | DateTime | Yes |
| **Completed At** | The timestamp when the audit session was finalized. | DateTime | No |
| **Created By** | The administrative user who launched the session. | Foreign Key | Yes |

## Reconciliation Actions
During an active session, auditing an asset logs an `AssetAudit` record containing the physical location observed, status label seen, auditor name, notes, and verification method. This automatically updates the parent asset's `last_audited` timestamp and historical activity logs.
