# Audit Sessions And Asset Audits

An **Audit Session** is a physical inventory reconciliation campaign. It defines the scope and lifecycle of an audit. Each scan or verification creates an **Asset Audit** observation for a specific asset.

For the operator workflow, see [Audits & Custody](../../features/audits-custody.md).

## Audit Session Fields

### Name

A descriptive name for the campaign, such as `Q2 Stockroom Audit`.

**Required:** Yes.

### Tenant

The customer tenant that owns the session when the audit is tenant-scoped. Broader audit scope is still limited by the operator's current authorization.

### Location

An optional location used to narrow the expected asset set.

### Status

The session lifecycle state. The current workflow uses planned, active, and completed states.

**Required:** Yes.

### Started At

When the audit session began.

### Completed At

When the session was finalized.

### Created By

The user who created the audit session.

### Reconciliation Report

The stored reconciliation result created when the session is completed.

## Asset Audit Fields

### Session

The audit session that contains the observation. Ad-hoc audit observations can exist without a parent session where the current workflow permits it.

### Asset

The asset that was observed. Historical audit evidence can remain after the asset record is no longer present.

### Auditor

The user who performed the verification.

**Required:** Yes.

### Timestamp

When the observation was recorded.

### Location

The location observed during the audit.

### Status

The asset status observed during the audit.

### Notes

Operator notes about the observation or discrepancy.

### Verification Method

How the asset was verified, such as a barcode scan or manual verification where supported by the current UI.

## Session Behavior

An asset is recorded at most once in the same session. Completing a session freezes its reconciliation result and prevents additional normal audit observations for that completed campaign.

The expected asset set depends on the audit scope. A location narrows the campaign, and tenant-scoped sessions stay within that tenant. Broader sessions remain limited to assets the acting user is authorized to access.

Recording an audit also updates the asset's most recent audit information. Do not treat an audit record as a legal certification by itself; it is operational evidence captured by ITAMbox.
