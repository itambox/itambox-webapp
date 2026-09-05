# Custody Receipts

A **Custody Receipt** is a materialized legal record documenting a user's acceptance of terms and custody for a specific physical asset. It acts as an audit trail detailing who signed for the hardware, when they signed, what specific version of the terms (EULA) they agreed to, and the technical metadata surrounding the transaction.

---

## Acceptance States

When an asset is checked out with **Require Acceptance** active, a Custody Receipt is generated in the **Pending** state. The receipt progresses through the following states:

- **Pending**: Awaiting user signature. The asset's assignment status is held in check.
- **Accepted**: The user has successfully signed the receipt. The asset is now officially in their custody.
- **Declined**: The user rejected the terms. Custody is returned, and administrative action is flagged.

---

## Fields

### Acceptance Method

Method used to record acceptance.

**Required:** Yes.

### Acceptance Status

The signature status (`pending`, `accepted`, or `declined`).

**Required:** Yes.

### Accepted

Whether acceptance was recorded.

**Required:** Yes.

### Accepted Date

Date and time acceptance was recorded.

### Asset

The physical hardware checked out.

**Required:** Yes.

### Created Date

Date and time the receipt was created.

### Custody Template

The Custody Template rules used to generate this receipt.

### Disclaimer

Disclaimer text presented with the receipt.

### EULA Text

The agreement text captured with the custody receipt, when applicable.

### EULA Version

Version tag (e.g., `1.0`) of the signed terms.

**Required:** Yes.

### Holder

The user or contractor taking custody.

**Required:** Yes.

### IP Address

The network address recorded with the acceptance event, when available.

### Qms Reference

Optional quality-management reference associated with the receipt.

### Signature Canvas

Base64 canvas stroke vector string representing the user's manual signature.

### Signature Data

Stored signature/acceptance data associated with the receipt.

### Signature Hash

Cryptographic SHA-256 hash of the signature image data.

### Signature Provider

Service handling the signature (e.g., `local` canvas or `docusign`).

**Required:** Yes.

### Signed At

The timestamp when the digital signature occurred.

**Required:** Yes.

### Token

Identifier used by the receipt workflow.

**Required:** Yes.

### User Agent

Browser header info logged at the time of signing.

### Verification Hash

Unique tracking verification string printed on audit exports.


## Verification & Audit Trails
Each receipt generates a **Verification Hash**. This hash can be verified by internal auditors to match the recorded IP, timestamp, user agent, and signature canvas vector directly back to the database record, ensuring tamper-proof compliance checks.
