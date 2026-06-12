# Scanning Assets

ITAMbox supports barcode and QR-code scanning in two modes: a **hardware scanner** connected to a desktop browser, and a **camera-based scanner** on mobile devices.

---

## Hardware / USB Barcode Scanners

Most USB / Bluetooth HID barcode scanners emulate a keyboard. To use one:

1. Open any asset audit session or the **Find by Scan** view (`/scan/`).
2. Click inside the scan input field so it has keyboard focus.
3. Configure your scanner to append **Enter** (newline) after each scan.
4. Scan the barcode — ITAMbox reads the input and resolves it automatically.

Supported code formats (handled by the resolve endpoint):

| Input format | Example | Notes |
|---|---|---|
| Bare asset tag | `IT-00042` | Case-insensitive |
| Serial number | `SN-ABC123` | Matched when no tag found |
| `itambox://` scheme | `itambox://tag/IT-00042` | Printed on QR labels |
| Full HTTP URL | `https://itam.example.com/assets/42/` | Scanned from printed QR code |

---

## Camera Scanning (Mobile)

ITAMbox can activate your device camera to scan barcodes and QR codes without a separate scanner app.

!!! warning "HTTPS required for camera access"
    Mobile browsers (iOS Safari, Android Chrome) only expose the camera API on **secure origins** (`https://`).  If ITAMbox is served over plain `http://`, the camera button will not appear and the browser will silently refuse the `getUserMedia()` call.

    **Production deployments must be served over HTTPS.** During local development you can use a self-signed certificate or a tool like [mkcert](https://github.com/FiloSottile/mkcert) to create a locally-trusted certificate.

To scan with the camera:

1. Open an audit session detail page or the **Find by Scan** view.
2. Tap **Scan with Camera** (the camera icon in the action bar).
3. Grant camera permission when prompted.
4. Point the camera at a barcode or QR code — ITAMbox decodes it automatically and resolves the asset.

---

## Find by Scan (`/scan/`)

The **Find by Scan** view resolves any supported code to the matching asset and redirects you to its detail page. Use it to:

- Quickly look up an asset from a printed label.
- Verify that a code is correctly linked to the expected record.
- Test scanner hardware before running an audit campaign.

If no asset matches, a 404 page is shown. Cross-tenant isolation is enforced: users only see assets belonging to their active tenant.

---

## Audit Session Scanning

During an audit campaign, the scan input is embedded in the audit session detail page. Each successful scan:

1. Resolves the code to an asset.
2. Records the asset as **physically verified** at the campaign location.
3. Updates the campaign's progress counters in real time (via HTMX).

Duplicate scans within the same session are deduplicated automatically.

---

## Action Bar Shortcuts

The scan action bar (visible on audit session pages) provides:

| Button | Action |
|---|---|
| **Scan Input** | Text field — type or paste a code, or let a HID scanner inject it |
| **Scan with Camera** | Activates the device camera (HTTPS required) |
| **Clear** | Resets the last scan result |

---

## Troubleshooting

**Camera button is missing or greyed out**
: The page is served over `http://`. Switch to `https://` or use mkcert for local dev.

**Scanner input is captured by the OS or another app**
: The scan input field must have keyboard focus. Click the field before scanning.

**Code resolves to the wrong asset**
: Check that the code format is supported (see table above). `itambox://` scheme codes are printed by the built-in label generator and are always unambiguous.

**"403 Forbidden" after a successful scan**
: The authenticated user lacks the `assets.view_asset` permission for the active tenant.
