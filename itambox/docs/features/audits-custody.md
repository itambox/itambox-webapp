# Audits And Custody

Audits help verify that recorded inventory matches what is physically present. Custody records help document who received or accepted responsibility for equipment and the terms presented at that time.

## Audit Sessions

Create an Audit Session for the tenant and scope you intend to verify. During the session, scan or select assets as they are found. Repeated scans of the same asset do not create duplicate verification records.

Audit scanning is tenant-bound. This differs from **Find by Scan**, which can search across all tenants the user is allowed to access.

Use the session's missing/flagged results to investigate assets that were expected but not verified. An audit result is evidence about the inventory process; it should not be treated as proof of facts the system did not actually capture.

## Custody Receipts

Custody templates define the text presented for a custody workflow. Receipts preserve the relevant acceptance information and terms captured by the application.

Do not describe a receipt as legally binding merely because ITAMbox stores it. Legal effect depends on jurisdiction, policy, identity assurance, and the specific process used by the organization.

## A Practical Audit Flow

1. Confirm the tenant and physical scope.
2. Open or create the Audit Session.
3. Scan assets as they are physically verified.
4. Investigate missing, unexpected, or ineligible records.
5. Correct inventory data only after confirming the real-world state.
6. Retain relevant custody, maintenance, or disposal evidence with the asset record.

See [Scanning Assets](../usage/scanning.md) and the [Audit Session Data Model](../models/compliance/auditsession.md).
