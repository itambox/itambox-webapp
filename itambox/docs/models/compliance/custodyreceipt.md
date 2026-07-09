# Custody Receipts

A **Custody Receipt** is a materialized legal record documenting a user's acceptance of terms and custody for a specific physical asset. It acts as an audit trail detailing who signed for the hardware, when they signed, what specific version of the terms (EULA) they agreed to, and the technical metadata surrounding the transaction.

---

## Acceptance States

When an asset is checked out with **Require Acceptance** active, a Custody Receipt is generated in the **Pending** state. The receipt progresses through the following states:

- **Pending**: Awaiting user signature. The asset's assignment status is held in check.
- **Accepted**: The user has successfully signed the receipt. The asset is now officially in their custody.
- **Declined**: The user rejected the terms. Custody is returned, and administrative action is flagged.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Acceptance Method** | The acceptance method of the custody receipt. | String | Yes |
| **Acceptance Status** | The signature status (`pending`, `accepted`, or `declined`). | Selection | Yes |
| **Accepted** | The accepted of the custody receipt. | Boolean | Yes |
| **Accepted Date** | The accepted date of the custody receipt. | Date Time | No |
| **Asset** | The physical hardware checked out. | Foreign Key | Yes |
| **Created Date** | The created date of the custody receipt. | Date Time | No |
| **Custody Template** | The Custody Template rules used to generate this receipt. | Foreign Key | No |
| **Disclaimer** | The disclaimer of the custody receipt. | Text | No |
| **EULA Text** | The exact copy of the legal terms signed by the user. | Text | No |
| **EULA Version** | Version tag (e.g., `1.0`) of the signed terms. | String | Yes |
| **Holder** | The user or contractor taking custody. | Foreign Key | Yes |
| **IP Address** | The IP address of the device used to sign the receipt. | IP Address | No |
| **Qms Reference** | The qms reference of the custody receipt. | String | No |
| **Signature Canvas** | Base64 canvas stroke vector string representing the user's manual signature. | Text | No |
| **Signature Data** | The signature data of the custody receipt. | Text | No |
| **Signature Hash** | Cryptographic SHA-256 hash of the signature image data. | String | No |
| **Signature Provider** | Service handling the signature (e.g., `local` canvas or `docusign`). | String | Yes |
| **Signed At** | The timestamp when the digital signature occurred. | DateTime | Yes |
| **Token** | The token of the custody receipt. | String | Yes |
| **User Agent** | Browser header info logged at the time of signing. | Text | No |
| **Verification Hash** | Unique tracking verification string printed on audit exports. | String | No |

## Verification & Audit Trails
Each receipt generates a **Verification Hash**. This hash can be verified by internal auditors to match the recorded IP, timestamp, user agent, and signature canvas vector directly back to the database record, ensuring tamper-proof compliance checks.
