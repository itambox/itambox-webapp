# Audit Sessions & Asset Audits

An **Audit Session** represents a physical inventory reconciliation campaign — a scheduled sweep to verify the existence, location accuracy, and operational health of assets across a site or globally. Each session produces individual **Asset Audit** records that capture the observed state of every asset scanned during the campaign, including its physical location, status label, auditor identity, and verification method.

---

## AuditSession

### Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | A clear, descriptive name for the audit campaign (e.g. `Q2 IT Stockroom Audit`). | String (200) | Yes |
| **Tenant** | The tenant this campaign belongs to. Leave blank for MSP-wide / global sessions. | Foreign Key | No |
| **Location** | Optional target site/location to scope the audit. If omitted, the campaign applies globally. | Foreign Key | No |
| **Status** | Session lifecycle state: `Planned`, `Active`, or `Completed`. | Choice | Yes |
| **Started At** | Timestamp when the audit session was opened (auto-set on creation). | DateTime | Yes |
| **Completed At** | Timestamp when the session was finalized and closed. | DateTime | No |
| **Created By** | The administrative user who launched the session. | Foreign Key | Yes |
| **Reconciliation Report** | Frozen JSON snapshot of the reconciliation report written at close time. Denormalised for long-term readability. | JSON | No |

### State Machine

- **Planned** → **Active**: The session is opened and auditors may begin scanning assets.
- **Active** → **Completed**: The session is finalized; a reconciliation report is generated and frozen.
- Once **Completed**, the session is immutable — no further audits may be recorded against it.

### Expected Assets

The `expected_assets_queryset` property determines which assets this session expects to audit:
- When a **location** is set, only assets physically assigned to that location are in scope.
- When **global** (no location), all deployable, pending, and deployed assets (excluding archived) are in scope.
- Tenant-scoped sessions additionally filter by the session's tenant.

---

## AssetAudit

### Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Session** | The parent audit session this record belongs to. May be null for ad-hoc / standalone audits. | Foreign Key | No |
| **Asset** | The physical asset being verified. SET_NULL on asset deletion preserves audit evidence. | Foreign Key | No |
| **Auditor** | The user who performed the verification. | Foreign Key | Yes |
| **Timestamp** | When the audit was recorded (auto-set on creation). | DateTime | Yes |
| **Location** | The **observed** physical location of the asset during the audit scan. | Foreign Key | Yes |
| **Status** | The **observed** physical status label of the asset at scan time. | Foreign Key | Yes |
| **Notes** | Free-text observations or findings from the auditor. | Text | No |
| **Verification Method** | How the asset was verified: `Barcode Scan`, `RFID Reader`, `Manual Input`, or `Agent API Handshake`. | Choice | Yes |

### Constraints & Behaviour

- **Unique per session**: An asset can only be audited once per session (`unique_session_asset` constraint).
- **Tenant attribution**: The changelog tenant is derived from `asset.tenant` (not the session), ensuring each audit change is visible to the asset's owning tenant even in global/MSP-wide sessions.
- **Orphan resilience**: When an asset is hard-purged, its audit records are preserved (SET_NULL on the asset FK) — compliance evidence survives asset destruction.
- **Side effects**: Recording an AssetAudit automatically updates the parent asset's `last_audited` timestamp and `last_audited_by` auditor reference.
