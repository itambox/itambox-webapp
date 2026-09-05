# Scanning Assets

ITAMbox can resolve asset tags, serial numbers, product codes, ITAMbox QR codes, and supported record URLs. Scanning is available for finding records, processing bulk asset actions, and verifying assets during an audit.

## Find By Scan

Use **Find by Scan** when you have a device or label but do not know which customer or record contains it.

1. Open **Find by Scan**.
2. Scan with a hardware scanner, use the camera action, or enter the code manually.
3. Review the matching record or result.

**Find by Scan searches across tenants the user can access even when another workspace is selected.** It never grants access to an inaccessible tenant.

A product code can identify a model rather than one physical item. When the same code can resolve to more than one asset, ITAMbox reports the ambiguity instead of selecting an arbitrary record.

## Hardware Scanners

Most USB and Bluetooth barcode scanners act like keyboards. Configure the scanner to send Enter after the scanned value, place the cursor in the scan field, and scan normally.

Rapid duplicate input is suppressed by the scanning interface. If a scanner behaves unexpectedly, test it in a text editor: the value should appear as ordinary text followed by Enter.

## Camera Scanning

Choose the camera action, allow camera permission, and hold the code in view until it is recognized. Browser camera access normally requires a secure HTTPS connection and can also be blocked by browser or device policy.

## Bulk Scan Baskets

ITAMbox provides **Bulk Check-in**, **Bulk Check-out**, and **Bulk Disposal** scan baskets.

1. Open the required bulk action.
2. Add assets by scanner, camera, or manual entry.
3. Review warnings and remove or correct ineligible rows.
4. Complete the operation fields.
5. Submit the basket.

Bulk checkout can warn that an asset is currently assigned and will be reassigned. Assets in repair, on order, or archived states are not eligible for checkout. Check-in warns when there is no assignment/location state to return. Disposal skips assets that are already disposed.

When the current workspace is **All Tenants**, the basket requires a concrete target tenant before it can resolve and mutate assets. A mixed aggregate view does not make a cross-tenant mutation ambiguous by design.

Submitting a scan basket creates a [Background Job](../features/background-jobs.md). Open the Job to review progress, completed rows, failures, and generated output.

## Audit Scanning

Audit sessions provide their own scan workflow. Each successful scan verifies the matching asset for that session. Audit sessions remain tenant-bound, unlike Find by Scan.

## Supported Resolution

For the exact lookup order and ambiguity behavior, see [Scan Code Resolution](../reference/scan-code-resolution.md).

## Troubleshooting

**Camera action unavailable:** confirm HTTPS and browser camera permission.

**Hardware scan does not submit:** focus the scan field and confirm the device sends Enter.

**No record found:** enter the asset tag or serial manually, verify access to the expected tenant, and inspect the printed code for damage.

**Action unavailable:** check the asset's lifecycle state and your permission for the selected tenant.
