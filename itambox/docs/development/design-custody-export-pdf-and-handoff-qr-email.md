# Custody receipt PDF export and handoff QR/e-mail delivery

Status: proposed design for issues #312 and #316  
Source snapshot: `design/issue-312-316-custody-export`, based on `origin/main` at `4b2a4d59`  
Date: 2026-08-10

## Context

The custody workflow already separates an internal operator from the intended recipient. An operator with `compliance.prepare_custodyreceipt` can prepare a 30-minute, one-time `CustodySigningSession`; the holder must then authenticate as the intended recipient and accept or decline. An accepted `CustodyReceipt` is available to an internal user only through asset-derived tenant scoping and object-level permissions.

Two demo findings extend that workflow:

1. The accepted-receipt export is currently JSON only. The JSON contract is useful for integrations and verification, but the primary operator action should produce a printable PDF.
2. The live handoff panel currently offers only a copy button. It should also support showing a QR code and sending the same short-lived link to the holder by e-mail.

The current executable contracts are:

- `_custody_receipt_export_payload()` in `compliance/views.py:248-310` produces deterministic `itambox.custody-receipt` version 1 JSON and deliberately omits `signature_canvas`, `signature_data`, and bearer tokens.
- `CustodyReceiptDetailView` in `compliance/views.py:543-592` scopes through `scope_custody_receipts()`, checks permissions against `receipt.asset`, exposes a signature image only for a PNG data URI, and renders a handoff URL only for the creating operator's live session.
- `CustodyReceiptExportView` in `compliance/views.py:621-644` requires `compliance.export_custodyreceipt`, returns `404` unless both `acceptance_status == accepted` and `accepted` are true, and sets attachment, `no-store`, and `nosniff` headers.
- `compliance/urls.py:34-45` names the existing routes `custodyreceipt_list`, `custodyreceipt_detail`, `custodyreceipt_prepare`, and `custodyreceipt_export`; the recipient route remains `custody_eula_sign` at line 56.
- `custodyreceipt_detail.html:16-23` already protects the native download with `hx-boost="false"` and `download`. Its handoff alert at lines 107-120 puts the complete bearer URL in one `data-copy-value` attribute.
- `core/reports/exporters.py:43-50` exposes `report_pdf_bytes(rendered_html)` and `PDF_MIME`. It delegates to `_html_to_pdf_bytes()` in `core/tasks/labels.py:418-426`.
- `_pdf_safe_link_callback()` in `core/tasks/labels.py:388-415` allows data URIs and approved static/media files while rejecting remote, `file://`, traversal, and other resource URLs.
- `send_notification_to_channel()` in `core/events.py:518-576` provides typed delivery results. Its e-mail branch at lines 473-515 loads the system-wide `EmailSettings`, creates an explicit SMTP connection, and classifies disabled configuration, missing recipients, transient transport failures, and terminal SMTP failures.
- `CustodyReceiptExportTests` in `compliance/tests/test_custody_rbac.py:1188-1302` freezes the JSON payload, headers, permission denial, accepted-state restriction, tenant isolation, and button visibility. The signing-session tests at lines 836-1141 freeze the operator-only handoff and token non-disclosure to other internal users.

The recommendations below preserve those boundaries. They do not make a QR code or e-mail a new authorization capability: they are alternate delivery mechanisms for a link the same operator is already allowed to copy.

## Goals

- Make PDF the one-click, user-facing export for an accepted receipt.
- Keep the JSON v1 bytes, filename, URL name, permission, and error contract intact.
- Produce a print-friendly PDF containing the asset, holder, accepted terms, signature attestation, and verification evidence.
- Add a self-hosted QR code without putting either bearer token in an image request URL or an unrelated user's HTML.
- Send the handoff link only to `receipt.holder.email`, using the existing SMTP delivery classification and configuration.
- Re-check the receipt, tenant, permission, operator, holder, and live-session state on every QR or e-mail request.
- Record the e-mail attempt and outcome without recording the URL, tokens, e-mail body, or address.
- Keep a durable, tenant-scoped delivery record per handoff e-mail (status, attempt, timestamps) so send bounds, deduplication, and audit correlation do not depend on cache state or journal-text parsing.
- Preserve CSP, translation, and native non-HTMX form/download behaviour.

## Non-goals

- Changing the JSON schema or removing machine-readable export.
- Providing public receipt verification or a QR-based verification service.
- Letting an operator choose or type a recipient address.
- Sending the handoff URL to a notification channel's administrator/broadcast recipients.
- Changing recipient authentication, the 30-minute session TTL, the seven-day link-only TTL, or one-time consumption.
- Persisting a QR image, bearer URL, e-mail body, or delivery token.
- A generalised notification outbox or automatic retry worker for all channels — the durable delivery record in Decision B6 covers custody handoff e-mail only, with deliberate manual retry.
- No migration-free constraint: Decision B6 adds one small migration for the durable delivery record (maintainer-approved 2026-08-11).

## Feature A: accepted receipt PDF export

### Decision A1: add a separate PDF route and preserve the JSON route

Recommendation: keep the existing route and view as the JSON contract, and add a sibling PDF route.

| Format | Method and path | URL name | Contract |
|---|---|---|---|
| JSON | `GET /custody-receipts/<pk>/export/` | `compliance:custodyreceipt_export` | Unchanged JSON v1 response and `custody-receipt-<pk>.json` filename |
| PDF | `GET /custody-receipts/<pk>/export.pdf` | `compliance:custodyreceipt_export_pdf` | New primary UI download and `custody-receipt-<pk>.pdf` filename |

Both views use the same scoped queryset, object-level `compliance.export_custodyreceipt` check, and accepted-state predicate. A small shared mixin/helper may centralize that lookup, but it must preserve the current check order: tenant scope hides a foreign receipt as `404`; a visible same-tenant receipt without permission returns the custody-specific `403`; an authorized pending/declined receipt returns `404`.

Rationale:

- Existing callers that reverse `custodyreceipt_export` and expect JSON continue to receive identical bytes, MIME type, disposition, and headers.
- The PDF tests can address a named route without query-string construction.
- Format-specific views keep content negotiation and unsupported-format errors out of a simple download surface.
- A suffix route matches the repository's explicit-format convention such as `audit-sessions/<pk>/report.csv` in `compliance/urls.py:21-25`. Note that the CSV sibling (`AuditSessionReportCsvView`, `views_audit.py:388-424`) sets neither `Cache-Control` nor `X-Content-Type-Options`; Decision A4 deliberately mirrors the stricter JSON export instead — do not copy the weaker sibling's headers.

Alternative: make `custodyreceipt_export?format=pdf|json` default to PDF. This has fewer URL declarations, but changing a bare request from JSON to PDF breaks the existing contract at the exact URL consumers already use. Keeping bare requests as JSON would also make the primary UI depend on a query string and would not deliver the issue's proposed default. A query parameter is therefore not recommended.

### Decision A2: use a dedicated Django template and an explicit PDF context

Add `compliance/templates/compliance/custodyreceipts/custodyreceipt_export_pdf.html`. Render it with Django's template engine and the current request, then pass the resulting HTML to `core.reports.exporters.report_pdf_bytes()`.

Do not construct a long HTML string in `views.py`. The repository already keeps substantial report HTML in templates, for example `templates/core/reports/polished_report.html`; a template gives the PDF normal auto-escaping, `{% translate %}` support, and maintainable print CSS.

The context builder should be an allowlist, not the entire model graph. Reuse `_custody_receipt_export_payload(receipt)` for the non-secret receipt, asset, holder, and session fields, then add only:

- the owning tenant name from `receipt.asset.tenant`, never the ambient tenant;
- a validated `signature_image` derived from `receipt.signature_canvas`; and
- localized labels/date formatting inputs.

Do not pass or render `receipt.token`, any signing-session token, or `signature_data`. Passing the complete `receipt` is convenient but makes later accidental template disclosure easier. The explicit context keeps the PDF boundary aligned with the JSON allowlist while making one intentional exception for the renderable signature image.

The signature helper should accept only a `data:image/png;base64,` prefix, strictly decode the payload, verify the PNG signature bytes, enforce a conservative decoded-size cap, and re-encode a canonical data URI. Invalid or oversized legacy data must omit the image and render “No renderable signature image is stored” rather than fail the whole export. The shared PDF callback already accepts data URIs (`core/tasks/labels.py:400-401`) and rejects network fetches.

Note the deliberate asymmetry: the JSON payload publishes `"omitted_sensitive_fields": ["signature_canvas", "signature_data", "token"]` (`compliance/views.py:257`) and the frozen test asserts the canvas is absent, while the PDF is the one intentional exception that embeds the renderable signature image. This is not new exposure — the internal detail view already renders the canvas under the *weaker* `view_custodyreceipt` (`compliance/views.py:559-561`), so the PDF under the stricter `export_custodyreceipt` is narrower, not wider. A handwritten signature is personal data, so record this exception explicitly here and keep the JSON contract's self-description accurate for the sibling route.

The PDF-only `<style>` block should use classes rather than `style=` attributes and should carry `nonce="{{ request.csp_nonce }}"`, matching the existing report template convention. The PDF bytes are not interpreted under browser CSP, but using the established authored-template form avoids introducing template/style-gate debt.

Alternative: interpolate an inline HTML string in the view. It avoids a file, but mixes escaping, legal-text pagination, layout, and HTTP behaviour, and is inconsistent with the repository's report template practice. It is not recommended.

### Decision A3: use a legal-record layout without a verification QR

Use A4 portrait, black/stone text, one restrained ITAMbox accent, 10-11 pt body text, high contrast, and table-based metadata blocks that xhtml2pdf handles reliably. Avoid flex/grid, remote fonts, external logos, and decorative elements that make printed evidence harder to read.

Recommended order:

1. **Repeated header** — owning tenant name, “Custody Receipt”, receipt `#<pk>`, and an Accepted marker.
2. **Asset and holder** — asset name, asset tag, serial number, asset ID; holder full name, UPN, e-mail, and holder ID.
3. **Terms accepted** — exact `eula_text`, followed by disclaimer, QMS reference, and EULA version. Preserve line breaks with an escaping filter; never use `safe`. Allow this section to break naturally across pages, and avoid a container-level `page-break-inside: avoid` that would overflow a long EULA.
4. **Acceptance attestation** — signed/accepted timestamps, acceptance method and status, signature provider, IP address, and user agent.
5. **Signature and hashes** — validated signature image at a bounded height, signature hash, and full verification hash in a wrapping monospace block.
6. **Handoff audit appendix** — the safe signing-session fields already present in JSON: operator username, intended holder ID/name, prepared/expiry/consumed/canceled times, and outcome. Never include a token.
7. **Repeated footer** — tenant name, receipt number, and page number. Do not insert a generation timestamp: the signed/accepted timestamps are the relevant evidence and omitting a volatile value makes repeated exports more reproducible.

Do not add a QR code for the verification hash in this slice. There is no verification URL or scanner flow that can turn it into an independently useful result, so a QR would look actionable while only returning an opaque hash. The full hash remains visible and copyable in the PDF. A later verified-receipt endpoint could justify a QR that points to a meaningful, permission-reviewed result.

Alternative: put a hash-only QR in the header. This is technically possible, especially because Segno is already present, but it consumes print space and has no defined resolver. It should be reconsidered only together with a verification-route design.

### Decision A4: mirror the JSON response headers

The PDF response is:

```text
Content-Type: application/pdf
Content-Disposition: attachment; filename="custody-receipt-<pk>.pdf"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Use `PDF_MIME` from `core/reports/exporters.py:12-13`. Do not set a public cache validator, store the file in media, or fall back to JSON when rendering fails. A renderer failure is caught by an explicit `except` around the render call: log with safe correlation fields only (`receipt_id`, `tenant_id`, `actor_id`, request ID, and exception type) and return an explicit `HttpResponseServerError` (500) without embedding receipt content or exception text — do not let the exception propagate through the generic 500 handler, which would surface less controlled output.

### Decision A5: make PDF the split-button primary action

Replace the single “Export” anchor with a Bootstrap/Tabler split button:

- primary anchor: “Download PDF”, new PDF route, `hx-boost="false"`, and `download`;
- adjacent dropdown toggle with an accessible “Other export formats” label;
- dropdown item: “Download JSON”, existing JSON route, `hx-boost="false"`, and `download`.

This preserves a one-click PDF while keeping JSON discoverable as a secondary artifact. The repository already uses `btn-group`, `dropdown-toggle`, and `dropdown-menu` patterns in its templates. A format toggle would introduce persistent UI state for two independent downloads and is not recommended.

### Feature A test plan

Extend `CustodyReceiptExportTests` in `compliance/tests/test_custody_rbac.py` rather than creating a disconnected permission fixture.

1. Keep `test_authorized_accepted_export_is_deterministic_json_without_secrets()` unchanged against `custodyreceipt_export`. This freezes the existing contract and filename.
2. Add an accepted-receipt PDF route test that calls `custodyreceipt_export_pdf` with the current tenant-admin fixture and asserts:
   - status `200`;
   - `Content-Type == application/pdf`;
   - `Content-Disposition == attachment; filename="custody-receipt-<pk>.pdf"`;
   - `Cache-Control == no-store`;
   - `X-Content-Type-Options == nosniff`;
   - bytes start with `%PDF`; and
   - byte length is greater than a small sanity threshold.
3. Add a template/input test that patches `report_pdf_bytes`, captures its `rendered_html` argument, and asserts the asset tag, holder full name, EULA, full verification hash, and safe signing-session fields are present. Assert both bearer tokens and raw `signature_data` are absent. This checks the data sent to the renderer without relying on PDF compression internals.
4. Add valid-signature, malformed-signature, and oversized-signature cases. The valid PNG data URI reaches the rendered HTML; malformed/oversized values produce the fallback text and still return a PDF.
5. Parameterize or duplicate the current pending, permission-denied, foreign-tenant, and superadmin cases for the PDF URL. Required results remain `404`, `403`, `404`, and `2xx` respectively, with no receipt payload in errors.
6. Render the detail template for an accepted authorized receipt and assert the PDF primary link and JSON secondary link both contain `hx-boost="false"` and `download`. Pending and unauthorized receipts contain neither.
7. Add a long-EULA renderer test that produces a multi-page PDF and completes without error; content membership is covered at the pre-PDF HTML layer.
8. Add a renderer-failure test: patch `report_pdf_bytes` to raise, and assert the response is a generic server error with no receipt content or exception text, and that the log line carries only safe correlation fields (`receipt_id`, `tenant_id`, `actor_id`, request ID, exception type). The differential-coverage gate requires the failure branch to be executed by a test.

The repository's existing PDF test does not parse PDF text: `core/tests/test_report_export_formats.py:27-31` asserts a `%PDF` prefix and a minimum byte length. Scheduled-report tests patch `report_pdf_bytes`. `pypdf` is not a direct dependency in `pyproject.toml`; its appearance in `uv.lock` is transitive through xhtml2pdf and must not be treated as an application/test API. The recommended two-layer test follows the existing pattern and avoids a new direct dependency.

## Feature B: handoff QR code and holder e-mail

### Dependency finding and decision

The issue premise that the repository has no QR dependency is not true for this source snapshot. `pyproject.toml:49` already declares `segno>=1.6,<2.0`, and `core/tasks/labels.py:172-202` already uses Segno to generate QR labels. Segno is a pure-Python, no-dependency QR generator with SVG stream output according to its [project metadata](https://pypi.org/project/segno/) and [serialization documentation](https://segno.readthedocs.io/en/stable/serializers.html).

Recommendation: reuse Segno and add no runtime dependency. Use `segno.make_qr()` rather than `segno.make()` so the result is a normal QR code, not a Micro QR code. Serialize a monochrome SVG to an in-memory byte stream with the standard four-module quiet zone and no title/description containing source data.

The proposed `qrcode` package is viable in isolation: its official [project documentation](https://pypi.org/project/qrcode/) includes `SvgPathImage`, and its SVG backend does not require Pillow. It is nevertheless redundant here and would add a second QR implementation plus `pyproject.toml`/`uv.lock` policy churn. A hand-written QR encoder or browser JavaScript library has a larger correctness/security surface and is not recommended.

Maintainer dependency-policy decision: confirm that reusing the already-approved Segno dependency is acceptable and update the issue premise. Only if Segno is intentionally removed before implementation should `qrcode` be reconsidered as a new reviewed direct dependency.

### Decision B1: use token-free server endpoints

Add two internal routes:

| Purpose | Method and path | URL name |
|---|---|---|
| QR image | `GET /custody-receipts/<pk>/signing-sessions/<session_pk>/handoff-qr.svg` | `compliance:custodyreceipt_handoff_qr` |
| Send e-mail | `POST /custody-receipts/<pk>/signing-sessions/<session_pk>/email/` | `compliance:custodyreceipt_handoff_email` |

Neither route contains `receipt.token`, `session.token`, the handoff URL, nor an e-mail address. The server reconstructs the absolute handoff URL only after authorization, from the database values and `request.build_absolute_uri()`.

Factor the current construction at `compliance/views.py:588-590` into one helper used by the detail view, QR view, and e-mail service. This prevents three subtly different URLs or encodings. The helper resolves the origin deterministically: prefer `settings.ITAMBOX_BASE_URL` (`core/settings/base.py:39`, documented in AGENTS.md/CLAUDE.md as "Public base URL for QR labels & outbound links"; currently consumed nowhere) when set, and fall back to `request.build_absolute_uri()`. Taking the origin from a request `Host` header is only as safe as `ALLOWED_HOSTS`, and deployments behind proxies commonly widen it — for e-mail the URL is composed and dispatched without any human seeing it, so a configured base matters more than for the copy button. The copy button, QR, and e-mail must all inherit the same helper so the operator and the holder never see different origins.

The setting becomes security-load-bearing and needs validation: an empty string means unset (fallback path); a non-empty value must be an absolute `http(s)` URL without trailing slash, enforced by a Django system check so a stale or wrong value cannot silently mail a live bearer credential to a host the deployment does not control. Re-document the setting as security-relevant (it is today described as a cosmetic label/link base). `scripts/check_contract_policy.py` inventories `ITAMBOX_*` reads and has no write mode — newly consuming the setting is likely a reviewed edit to `docs/development/external-contract-inventory.md`.

Behaviour change note: with the setting unset — the default, and the test environment — the copy button keeps today's request-derived URL, so the existing signing-session tests (`test_custody_rbac.py:836-1141`) stay green unchanged; new `ITAMBOX_BASE_URL` cases cover QR and e-mail only.

Both routes share a live operator-session resolver with this order:

1. Require authentication.
2. Resolve the receipt through `scope_custody_receipts()` using the asset tenant.
3. Require `compliance.prepare_custodyreceipt` on `receipt.asset`.
4. Resolve `session_pk` under that receipt with `CustodySigningSession.objects` — the tenant-scoping manager (`compliance/models.py:226-228`, `tenant_lookup = "receipt__asset__tenant"`). Do not copy `_base_manager` from the adjacent recipient-token path; that would lose the tenant filter.
5. Require `session.operator == request.user` and `session.intended_holder_id == receipt.holder_id`.
6. Require `consumed_at` and `canceled_at` to be null and `expires_at > now`.
7. Require `receipt.acceptance_status == CustodyReceipt.STATUS_PENDING` and `receipt.holder_id is not None`, so the endpoint gate is identical to `can_prepare_signing_session` (`compliance/views.py:569-573`). This matters because a session is consumed only on the assisted POST path (`_consume_custody_signing_session`, reached from `_process_custody_post`); a holder who accepts or declines through the plain bearer link leaves the prepared session `active` for up to 30 minutes. Without this step the endpoints would mint a QR or send an e-mail containing a live handoff credential for an already-accepted or already-declined receipt — a link the detail page deliberately stopped rendering.

The pending-state gate is deliberately the *last* step, after operator ownership: a non-owner probing a terminal receipt must receive the neutral `404` from step 5, never a `410` that distinguishes accepted/declined from pending. The mint path still passes through the gate before any credential is produced.

The endpoints must not trust that the detail page was authorized moments earlier. A permission revocation, active-tenant switch, session expiry, consumption, cancellation, holder change, or different operator between page render and click must deny the new request.

Response classes:

| Situation | Result |
|---|---|
| Anonymous caller | Existing login redirect |
| Same-tenant caller lacks prepare permission | Custody-specific `403` |
| Receipt is outside authorized tenant scope | Neutral `404` |
| Session is absent, bound to another receipt/holder, or owned by another operator | Neutral `404` |
| Known operator session is expired, consumed, or canceled | `410` without receipt/token payload |
| Receipt is already accepted or declined (terminal state) while a session is still active | `410` without receipt/token payload — state expiry, not a lookup failure |

The internal `410` needs its own error surface. The existing `410` rendering path (`custody_session_expired_or_used` through `sign_error.html`, `compliance/views.py:164-179`) is recipient-facing; per `custody-permissions.md` §4.2 the internal/recipient error split is load-bearing and tested. Reuse the internal error surface (`internal_permission_error.html` pattern) for the internal endpoints, with status `410` and a user-visible string that distinguishes the state ("Custody handoff session expired or no longer available" — no receipt/session identifiers), and assert in the tests that the recipient error code never appears on an internal route.

The operator ownership check is essential. An administrator who can view the signing-session audit but did not create the session currently receives no bearer URL (`test_custody_rbac.py:878-890` and `:1115-1141`); the QR and e-mail endpoints must not become an alternate recovery path.

Alternative: put the complete handoff URL in an endpoint query parameter. This leaks both tokens to access logs, proxy logs, browser history, monitoring, and referrers, and is rejected. An endpoint keyed only by receipt/session IDs but lacking permission and operator checks is also rejected.

### Decision B2: return SVG from the endpoint; do not embed a data URI in the page

The QR view asks the service layer to render the reconstructed URL and returns:

```text
Content-Type: image/svg+xml
Content-Disposition: inline; filename="custody-handoff-<session_pk>.svg"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Do not write the SVG to the database, media storage, filesystem, or shared cache. It is cheap to regenerate, secret-derived, and valid only while the session is live. `no-store` applies to browsers and intermediaries; the view must also avoid an ETag.

The service produces the SVG with Segno and fixed server-controlled rendering options. Keep those options in one shared helper (error level, quiet zone, version cap, serializer options) next to the existing label QR generation in `core/tasks/labels.py:172-202` or alongside `core/reports/exporters.py`, so QR policy is not split across two layers with drifting options. User data is encoded into the QR matrix, not interpolated into markup attributes, titles, or descriptions. Tests should additionally assert that the SVG bytes do not contain either token as literal text.

SVG safety is layered: an `<img>`-referenced SVG never executes script, and `CSPMiddleware` sets its headers on every response without content-type gating (`itambox/middleware.py:124-160`), so even a direct navigation to the SVG inherits `script-src 'self' 'nonce-…'` with no `unsafe-inline`. As belt-and-braces for an inline-disposition SVG endpoint, additionally return a response-level `Content-Security-Policy: default-src 'none'` and assert in the tests that the bytes contain no `<script` or `<foreignObject`.

The endpoint is preferable to an inline SVG/data URI because:

- the detail HTML stays small and keeps the existing literal token count at one URL occurrence;
- page/source capture does not gain a second token-derived blob;
- the image has its own authorization and freshness check; and
- the browser request path contains only non-secret IDs.

`CSPMiddleware` currently allows same-origin images and data images via `img-src 'self' data:` (`itambox/middleware.py:129-152`), so the endpoint requires no CSP relaxation. No external QR service is permitted.

Alternative: call Segno's SVG data-URI helper in `CustodyReceiptDetailView` and bind it directly to `<img src>`. This avoids a second HTTP request and is CSP-compatible, but duplicates a secret-derived artifact in the detail response, increases HTML size, and cannot re-check expiry when the image is fetched. It is not recommended.

### Decision B3: use a compact show/hide QR action

Keep all three actions in the existing info alert:

- “Copy recipient handoff link” remains the first, primary action and keeps `data-copy-value`/`data-copy-feedback`.
- “Show QR code” is a secondary button using Bootstrap collapse data attributes (`data-bs-toggle`, `data-bs-target`, `aria-controls`, and `aria-expanded`), requiring no new inline or custom JavaScript.
- “E-mail link to holder” is a secondary CSRF-protected POST form button.

The collapsed region contains the same-origin QR `<img>`, a translatable accessible description, the exact expiry time, and a warning that the code is a one-time credential for the intended holder. Do not put the handoff URL in alt text, title text, captions, or data attributes. Size the QR from the symbol's module count rather than a fixed pixel value: the handoff URL is roughly 185-190 characters with the default origin (two 64-char tokens plus path and host), which places the symbol around version 9-10 (53×53 to 57×57 modules); with the four-module quiet zone that is ~61-65 modules, so target a minimum module pitch of ~3 px (≈0.8 mm) and cap the displayed size responsively instead of hard-coding 192 CSS px. With a configured `ITAMBOX_BASE_URL` the prefix is deployment-controlled and can exceed that budget — Segno then picks a higher version and the symbol renders smaller, which is acceptable. The version cap is therefore a test assertion over the default origin (so a future URL-length change cannot silently produce an unreadable symbol), never a runtime constraint that could 500 the endpoint on a long-hostname install.

The alert and all three actions remain conditional on the same live operator session. If the holder lacks an e-mail address, render the e-mail action disabled with a translatable explanation. If system e-mail is disabled, render the same safe disabled state (“E-mail is not configured”) without exposing SMTP host, account, or channel configuration. The POST action repeats those checks to handle races.

Showing the QR automatically is an acceptable alternative for an onsite-only screen, but it makes the credential visible whenever the detail page is open. The explicit toggle better preserves the current copy-first hierarchy and the issue's “optionally show” wording.

Known trade-off: browsers fetch `<img>` subresources inside `display:none` containers on page load, so the QR SVG is generated and transmitted on every detail-page render whether or not the operator expands it — the endpoint's freshness check therefore fires at page load, not at reveal. Accepting this keeps the change script-free; the page is permission-gated and the response is `no-store`. If lazy generation is required later, `src` can be populated from `data-src` on `shown.bs.collapse` at the cost of a small script.

### Decision B4: send directly to the holder through the existing delivery implementation

The handoff recipient is not a configurable audience. It is exactly `receipt.holder.email`, because the URL is bound to `session.intended_holder`. The POST body must not contain an address, subject, body, or URL. Do not fall back to `EmailSettings.test_recipient`, `from_address`, the operator, or a `NotificationChannel.config['recipients']` list.

`NotificationChannel` is tenant-scoped (`extras/models.py:1014-1066`), but an e-mail channel's `config['recipients']` is a broadcast destination list. Reusing that list would commonly send the holder's bearer link to tenant administrators, contradicting recipient binding. `EmailSettings` is intentionally a system-wide SMTP singleton (`core/models.py:658-710`); it is the transport configuration, not the recipient policy.

Recommendation: factor the body of `_send_email_notification()` in `core/events.py:473-515` into a public recipient-aware helper, for example:

```text
send_email_notification(recipients, subject, body, *, tenant_id) -> DeliveryResult
```

Then keep `send_notification_to_channel()` as a compatibility wrapper that calls the helper with the channel's configured recipients, while the custody service calls the same helper with `[receipt.holder.email]`. This reuses the existing `EmailSettings`, explicit SMTP connection, safe logging, and `DeliveryResult` classification without inventing an unsaved/fake channel or misusing a configured audience.

The e-mail is plain text. It is localized for the **recipient**, not the operator: resolve the language from the holder's linked account via `holder.user.preferences.data.get("language")` (the `UserPreference` JSON store, `users/models.py:94-131`; same lookup and validation logic as `users/forms.py:189-201`), falling back to `settings.LANGUAGE_CODE` when the holder has no linked user or no stored language, and wrap subject/body construction in `django.utils.translation.override(...)`. There is deliberately no tenant-default rung: the tenant has no language setting today, and inventing one would be a product decision outside this feature. The instructions ("sign in as the intended holder", ignore/report guidance) are security content for a different person in a multilingual tenant; the operator's active language is not a safe proxy. It contains:

- a custody-action subject with the asset name and tag;
- the holder's name;
- asset name, tag, and serial number;
- the complete handoff URL;
- the absolute session expiry time;
- an instruction to sign in as the intended holder; and
- an ignore/report instruction if the message was unexpected.

It does not contain the EULA, signature fields, verification hash, SMTP configuration, or unrelated receipt/session history.

Header-safety: Django's `EmailMessage` header sanitization raises `BadHeaderError` (a `ValueError`) when a subject contains a newline — reachable via asset names imported through CSV or API, which do not use a `TextInput`. The factored core helper must classify `ValueError`/`BadHeaderError` as `TERMINAL`, and the custody service must additionally normalize the subject (collapse whitespace, cap length) before composing the message, so a hostile asset name can never 500 the synchronous POST.

Send synchronously. A queued task containing the complete URL would persist bearer material in django-q arguments; a task containing IDs would need to reconstruct the URL and could run after the 30-minute session expires. Synchronous delivery also gives the operator an immediate classified result.

Delivery UX:

- success: redirect to receipt detail with a success message that includes the remaining session TTL (see the latency note below);
- disabled/missing configuration or terminal SMTP rejection: redirect with the safe `DeliveryResult.user_message`;
- retryable/ambiguous transport failure: redirect with “Delivery could not be confirmed; the session remains active” and allow a deliberate manual retry;
- never show raw exception text, the URL, or an SMTP response.

Response shape: the detail page is globally htmx-boosted, so the e-mail `<form>` must carry `hx-boost="false"` (like the download links) and be handled as a native POST. The disposition-to-response mapping for the e-mail action:

| Outcome | Native POST (hx-boost="false" form) | HTMX request (if ever reused) |
|---|---|---|
| Success | Redirect + success message incl. remaining TTL | `204 + HX-Trigger` |
| Retryable/unconfirmed | Redirect + "Delivery could not be confirmed; the session remains active" (info message, not success) | Error toast |
| Terminal/disabled (missing config, SMTP rejection, holder without e-mail) | Raise `ValidationError` → redirect + error message (never a success toast) | Error toast |
| Bound refused ((N+1)th send) | Redirect + safe "too many attempts" message | Error toast |
| Session/receipt state failure (`410`) | Internal `410` error page (redirects are meaningless for a dead session) | Internal `410` error page |

The e-mail view must raise `ValidationError` for terminal/disabled delivery states instead of returning normally, otherwise the base view's unconditional `messages.success()` would render a failure as a success toast.

Send bound: the POST endpoint is an abuse surface, not just an idempotency question. `prepare_custodyreceipt` is seeded to the Technician role, so any technician could POST in a loop and drive unbounded outbound mail to one holder's address through the install's SMTP credentials. The bounds are enforced by the durable delivery record (Decision B6) — counting is race-safe through the session row lock, survives restarts, and never depends on cache state:

- at most 3 attempts per signing session (delivery rows with `status in (requested, succeeded, terminal_failed)`); a refused POST books no attempt and no SMTP call;
- at most 6 attempts per receipt (count over `signing_session__receipt`), so preparing a fresh session (or letting one lapse and re-preparing) cannot reset the volume bound for the same handover;
- a short per-operator cooldown between sends remains a `itambox/ratelimit.py` cache key (throttle only).

SMTP latency vs. TTL: `DeliveryDisposition.SUCCESS` means the SMTP server accepted the message, not that it was delivered — greylisting and queueing routinely add 15+ minutes, a large fraction of the 30-minute session TTL. The absolute expiry is already in the body; the operator-facing success message should also surface the remaining TTL so the operator knows whether to stay and re-send.

Do not automatically retry. A timeout can occur after the server accepted the message, so automatic retry risks duplicate bearer-link mail. A later transactional outbox design could address this with an idempotency model, but that is outside this feature.

Alternatives:

- Calling Django `send_mail()` directly, as `assets/services.py:182-231` currently does for initial signature requests, is not recommended. That path has a fallback to a test/from address and bypasses the channel delivery result contract; neither is acceptable for a short-lived recipient-bound credential.
- Extending `send_notification_to_channel()` with a recipient override is workable, but still requires choosing an arbitrary configured channel when several exist. A factored recipient helper expresses the actual transport boundary more clearly.
- `mailto:` avoids server SMTP and audit/error handling, exposes the bearer URL to a local mail client/draft, and cannot confirm delivery. It is not recommended as the feature implementation.

### Decision B5: journal the attempt and outcome on the asset

Do not call `dispatch_event(CustodyReceipt, ..., action="update")` for e-mail delivery. `dispatch_event()` (`core/events.py:105-120`) records only a generic model/action payload (`{"app_label", "model_name"}` at `:117`) and does not encode the operator, intended session, or delivery outcome. Event-rule firing is content-type-scoped, tenant-scoped, and action-membership-checked (`process_event_rules`, `core/events.py:123-162`); the residual risk is a *global* (`tenant=None`) rule on the same ContentType+action, whose JSON conditions cannot discriminate further because `event.data` carries no field values. Either way the payload is too generic for this audit.

Use the existing `extras.JournalEntry` pattern instead. `JournalEntry` derives its tenant from its content object (`extras/models.py:419-471`) and sets `allow_global_tenant = True` (`:425`), so attach it to `receipt.asset`, whose tenant is authoritative. Attaching it directly to `CustodyReceipt` or `CustodySigningSession` would currently derive a `None` tenant and — because of `allow_global_tenant` — become visible to every tenant (`core/managers.py:367`); that is strictly worse than the asset anchor.

Recommended flow:

1. Before contacting SMTP, create an asset journal entry with the authenticated operator as `user` and a neutral comment such as “Custody handoff e-mail delivery requested for signing session" — without receipt or session IDs (see the visibility decision below). If this audit write fails, do not send. The entry must be committed in its own transaction before SMTP is contacted: inside the request-level transaction alone it is not durable and would roll back with the response, and a concurrent POST could not see it either (the "durable row" claim in the next paragraph depends on this commit boundary).
2. Deliver synchronously.
3. Update the same entry to record `succeeded`, `retryable/unconfirmed`, or `terminal failure`, plus the session expiry. The journal timestamp/user supply who and when.

A bound-refused POST (delivery-bound exhausted, Decision B6) writes its own journal entry marked `refused` — an abuse trace is useful — but a refused POST books no delivery row, so it cannot amplify the bound (see Send bound in Decision B4).

Known correlation limit: with the IDs redacted, the entry's operator, timestamp, and asset locate the receipt for the single-receipt case; for an asset with serial or concurrent pending receipts, two sends by the same operator in the same window are not individually distinguishable in the journal. Decision B6's `CustodyHandoffDelivery` rows close this gap for authorized users — they are session-bound and appear in the receipt detail's signing-session table; the journal remains the tenant-visible trace for `view_asset`-level users.

Never put the holder address, subject/body, receipt token, session token, complete URL, SMTP response, or receipt/session IDs in the comment. The asset journal is tenant-scoped and visible through the established audit surface, but that surface is reachable with `view_asset` alone — and `custody-permissions.md` §2.3 is explicit that `view_asset` must not grant internal receipt visibility. Receipt/session IDs in the comment would hand custody-workflow metadata to principals the binding policy deliberately excludes. Omitting the IDs keeps the audit reconstructable for authorized users within the correlation limit above: the entry's operator, timestamp, and asset locate the receipt, and the exact session appears in the receipt detail's signing-session table for anyone with `view_custodyreceipt`. Decision B6's delivery rows make that correlation exact. If maintainers prefer the IDs in the journal as well, that is a deliberate §2.3 carve-out that must be recorded here and covered by a test asserting what a `view_asset`-only, non-`view_custodyreceipt` principal sees after a send.

### Decision B6: durable delivery record (maintainer-approved 2026-08-11)

Maintainer decision on open question 5: the durable idempotency/cooldown model is part of this feature, not a separate change. Add one small model and one migration:

`CustodyHandoffDelivery` in `compliance/models.py`:

| Field | Meaning |
|---|---|
| `receipt` (FK) | The custody receipt; tenant derived via `receipt.asset.tenant` |
| `signing_session` (FK) | The prepared session the e-mail belonged to |
| `operator` (FK) | The authenticated operator who requested the send |
| `attempt` (positive int) | 1-based send-attempt number for this session |
| `status` | `requested` / `succeeded` / `terminal_failed` |
| `error_class` | Nullable classification string (`SMTPAuthenticationError`, `timeout`, …) — never raw exception text, SMTP responses, or configuration values |
| `delivered_at`, `created_at`, `updated_at` | Timestamps; `delivered_at` only for `succeeded` |
| `unique_together (signing_session, attempt)` | Deduplication: a double-submitted or concurrent POST cannot book the same attempt twice |

The row stores no token, URL, e-mail address, subject, body, or SMTP detail. The manager is tenant-scoped like `CustodySigningSession` (`tenant_lookup = "signing_session__receipt__asset__tenant"`, `deny_global_tenant = True`); the model is not a new authorization surface — reads go through the service layer under the same `prepare_custodyreceipt` + operator-ownership checks as the endpoints, and the receipt detail's signing-session table shows delivery status to `view_custodyreceipt` holders.

Send flow (replaces the cache-counting bound in Decision B4):

1. `transaction.atomic`: lock the signing session row (`select_for_update`), count delivery rows for the session where `status != refused` semantics apply — attempts are rows with `status in (requested, succeeded, terminal_failed)`; if the count is already 3, refuse (journal `refused` entry, no delivery row, no SMTP call). Same for the per-receipt bound (6) via `signing_session__receipt`.
2. Create the delivery row `attempt=<count+1>, status="requested"` and commit — this is the durable "attempt started" marker, independent of the request-level transaction.
3. Contact SMTP synchronously.
4. In a short own transaction, update the row to `succeeded` (+`delivered_at`) or `terminal_failed` (+`error_class`).

If the process dies after SMTP acceptance but before step 4, the row conservatively stays `requested` — never claimed successful, never silently re-sent (a manual retry books a new attempt). The operator cooldown between sends remains a short-lived `itambox/ratelimit.py` cache key; it is a throttle, not the audit.

Benefits over the pure-cache bound: the counters survive restarts and are race-safe via the row lock; the receipt-bound no longer needs a separate cache key (count over `signing_session__receipt`); audit correlation for `view_custodyreceipt` holders is exact (delivery rows are session-bound, closing the journal correlation limit in B5); and a `refused` attempt leaves a durable trace without counting.

### Feature B security analysis

#### Bearer-token handling

- The only literal URL in detail HTML remains the existing `data-copy-value`. The QR `<img>` URL and e-mail form action contain only receipt/session primary keys.
- The QR response necessarily represents the full URL in its pixels, and the e-mail necessarily contains it in the body. Neither response includes it as a request URL, log field, SVG metadata string, audit comment, cache key, or stored file.
- Core delivery logging already uses safe correlation fields and does not log subject/body (`core/events.py:44-72`, `:473-515`). The custody boundary adds only safe object IDs and exception types.
- Other internal viewers keep seeing the signing-session audit table but cannot call either delivery endpoint for a session they did not create.

#### Tenant and object isolation

- Receipt lookup always starts with `scope_custody_receipts()` (`compliance/services.py:4-21`) and permission is checked on `receipt.asset`.
- Session lookup is nested under the already-scoped receipt and validates operator and intended holder.
- Foreign receipt/session identifiers return `404` without asset, holder, EULA, e-mail, URL, or token data.
- The asset, not the holder's ambient tenant or request context alone, remains the authorization and journal anchor.

#### CSP and active content

- The detail change uses no inline JavaScript and relies on existing copy handling plus Bootstrap data attributes.
- The QR is a same-origin image allowed by the existing CSP; no new origin or `data:` relaxation is needed.
- SVG is produced by a trusted serializer with fixed options and served with `image/svg+xml`, `nosniff`, and `no-store`. No user-authored SVG markup is accepted.
- The PDF uses the SSRF-safe renderer callback and never fetches a remote logo, font, signature, or stylesheet.

#### E-mail recipient and configuration

- The server derives one recipient from the session-bound holder. There is no form field or fallback recipient.
- `EmailSettings.load()` is system-wide, not tenant-specific (`core/models.py:708-710`). Tenant ID is used for permission and safe log correlation, not to infer a different SMTP account.
- Disabled e-mail, absent holder address, SMTP rejection, and timeout are normal classified states. None may invalidate or consume the signing session.
- Sending a message does not extend the expiry or create a new signing session.

### Feature B test plan

Add the tests beside `CustodySigningSessionPrepareTests` and `CustodySigningSessionAuditTests` in `compliance/tests/test_custody_rbac.py` so they reuse the existing roles, tenants, holder binding, and neutral error assertions.

#### Shared page and live-session tests

1. A live session created by the current operator renders copy, show-QR, and e-mail controls, the QR endpoint URL, the e-mail POST URL, and the expiry.
2. Pending receipt without a live session, expired/consumed/canceled session, completed receipt, and different operator render none of the delivery controls.
3. Assert the receipt token and session token each occur exactly once in the detail body: in the existing `data-copy-value` URL. The QR endpoint and e-mail action must not add occurrences. The current test at `test_custody_rbac.py:870-876` checks presence but not count; strengthen it rather than weakening it for QR.
4. Assert the copy and collapse buttons retain the expected CSP-safe data attributes and the e-mail action is a POST form with CSRF protection.

#### QR endpoint tests

1. Permitted creating operator with a live session receives `200`, `Content-Type: image/svg+xml`, inline disposition, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`.
2. Patch the QR service boundary and assert the encoded value is the exact absolute `custody_eula_sign` URL with both server-derived tokens; with `ITAMBOX_BASE_URL` set, assert the encoded URL uses the configured base instead of the request host.
3. With real Segno output, assert an SVG root/path exists, bytes are non-empty, neither token occurs literally in the SVG, the QR version stays within the declared cap, and no `<script` or `<foreignObject` appears; assert the response carries the `default-src 'none'` CSP header.
4. Same-tenant user without prepare permission receives the internal `403`; foreign tenant, other operator, mismatched receipt/session, and holder mismatch receive neutral `404`; expired/consumed/canceled session receives `410`; an accepted or declined receipt with a still-active session also receives `410`.
5. Assert no denied response contains asset, holder, EULA, address, or either token.

#### E-mail action tests

1. Patch the recipient-aware core helper, POST as the creating operator, and assert the recipients argument is exactly `[holder.email]`; subject/body contain the expected asset and reconstructed URL; no caller-supplied recipient can alter it. With `ITAMBOX_BASE_URL` set, assert the URL in the body uses the configured base.
2. Return each `DeliveryDisposition` and assert the redirect/message mapping (HTMX: `204 + HX-Trigger` or error toast; native: redirect; terminal/disabled states raised as `ValidationError`, never a success message), unchanged session expiry/state, and safe journal outcome.
3. Assert no fallback send occurs when the holder has no e-mail, even if `test_recipient` or `from_address` is populated.
4. Assert disabled/missing `EmailSettings` produces a disabled UI and a safe POST result without a crash.
5. Assert unauthorized same-tenant POST is `403`, foreign receipt/session is `404`, terminal session and terminal (accepted/declined) receipt are `410`, and the delivery helper is never called.
6. Assert the (N+1)th send within the per-session bound is refused without calling the delivery helper; that preparing a fresh session for the same receipt cannot exceed the per-receipt bound; that the per-operator cooldown rejects a second session's POST within the cooldown window; and that a refused POST writes a `refused` journal entry that does not count toward any bound.
7. Assert the rendered subject/body language follows the holder's preference (or `LANGUAGE_CODE`), not the operator's active language.
8. Assert journal entries use the asset tenant and operator, contain disposition and expiry only, contain no address, subject/body, URL, tokens, or receipt/session IDs; assert what a `view_asset`-only, non-`view_custodyreceipt` principal sees on the asset journal after a send.
9. Assert retry is manual: no django-q task, schedule, or persisted task arguments are created.
10. Delivery-record tests: a successful send creates one `CustodyHandoffDelivery` row with `attempt=1, status=succeeded` and `delivered_at`; a terminal SMTP failure updates the same row to `terminal_failed` with a classified `error_class` (never raw exception text); a retry books `attempt=2`; `unique_together (signing_session, attempt)` rejects a double-booked attempt.
11. Bound tests over the model: after 3 session attempts the 4th POST is refused without an SMTP call and without a new delivery row; a fresh session for the same receipt stops at the 6-attempt receipt bound; a refused POST creates no delivery row and a `refused` journal entry.
12. Concurrency test: two threads POST concurrently; the `select_for_update` session lock admits exactly one attempt at a time (or the second is refused) and never produces duplicate attempt numbers; assert no row with `status="requested"` is ever reported to the operator as success.
13. Tenant/scope test: delivery rows are invisible outside the session's tenant (manager scope), and a foreign-tenant receipt/session cannot be used to read or count delivery rows.

The requested “override `EMAIL_BACKEND` to locmem” pattern does not exercise the current notification-channel e-mail implementation by itself. `_send_email_notification()` explicitly requests `django.core.mail.backends.smtp.EmailBackend` at `core/events.py:488-497`. Existing delivery tests therefore patch `get_connection` (`core/tests/test_delivery_contracts.py:228-255`), and the custody failure test in `assets/tests/test_assignments.py:100-102` patches `send_mail`; it does not use locmem. The implementation should follow those actual conventions: mock the core delivery helper in custody route tests and test SMTP classification at the factored core helper with a mocked connection. A locmem outbox assertion is appropriate only if the factored helper is deliberately designed to accept an injected/test connection; it must not silently replace production `EmailSettings` behaviour merely to simplify a test.

## Implementation map

| File | Planned responsibility |
|---|---|
| `compliance/models.py` | Add `CustodyHandoffDelivery` (tenant-scoped manager, `unique_together (signing_session, attempt)`, status choices) + migration |
| `compliance/urls.py` | Add named PDF, QR SVG, and e-mail POST routes; keep the JSON route unchanged |
| `compliance/views.py` | Add PDF/QR/e-mail HTTP views; reuse internal custody error handling; keep route lookup and permission order explicit |
| `compliance/services.py` | Centralize handoff URL construction (origin from `ITAMBOX_BASE_URL` with `build_absolute_uri` fallback), live operator-session validation (incl. receipt-pending gate), QR SVG generation via the shared Segno options helper, send-bound enforcement, and recipient-bound delivery orchestration |
| `compliance/templates/compliance/custodyreceipts/custodyreceipt_export_pdf.html` | New auto-escaped, translated, xhtml2pdf-friendly receipt document |
| `compliance/templates/compliance/custodyreceipts/custodyreceipt_detail.html` | Split PDF/JSON export control and secondary copy/QR/e-mail handoff actions |
| `core/events.py` | Factor the existing channel e-mail implementation into a recipient-aware helper while retaining channel compatibility |
| `compliance/tests/test_custody_rbac.py` | End-to-end route, RBAC, tenant, state, token, header, UI, and audit tests |
| `core/tests/test_delivery_contracts.py` | Recipient-aware helper delivery classifications and safe-log tests |
| `core/tests/test_report_export_formats.py` or custody tests | Keep one real xhtml2pdf smoke assertion using `%PDF` and byte length |

One new model and its migration are required (Decision B6). No REST serializer, OpenAPI schema, GraphQL schema, background task, or new frontend bundle is required by the recommendation.

## Rollout and operational notes

- There is one new migration (`CustodyHandoffDelivery`, Decision B6). It is additive; no data migration, no backfill. Existing accepted receipts export immediately; invalid legacy signature images degrade to text while all other evidence remains available.
- There is no new dependency under the source snapshot because Segno is already declared and xhtml2pdf is already in the mandatory `[project].dependencies` array (`pyproject.toml:49`, `:55`; "on-demand" refers to the lazy import in `core/tasks/labels.py:420`, not an optional install group — the file has no `[project.optional-dependencies]` table). If maintainers choose `qrcode` instead, both `pyproject.toml` and `uv.lock` must change under the direct-dependency policy.
- Existing bookmarks/integrations for `custodyreceipt_export` continue to download JSON. The UI alone changes its primary target to the new PDF route.
- New strings in Python and templates use `_()`, `{% translate %}`, or `{% blocktranslate %}`. No German translation is required for implementation acceptance, but strings remain extractable.
- Production SMTP can be configured in two places today: environment settings in `core/settings/prod.py:72-84` and the `EmailSettings` singleton consumed by notification channels. This design follows the notification-channel path because it supplies classified failures. The maintainer should document that distinction if it remains intentional.
- With the helper preferring `ITAMBOX_BASE_URL`, operators should set it to the public origin in production; alternatively keep `ITAMBOX_ALLOWED_HOSTS` (`core/settings/prod.py:23`) strict — the request-derived fallback path depends on it, so it must never be widened to a wildcard.
- Development points the default backend at Mailpit (`core/settings/dev.py:19-24`), but channel delivery still uses `EmailSettings`; the UI must say “not configured” until that singleton is enabled.
- PDF and QR responses are synchronous and non-cacheable. Their expected sizes/work are bounded; no artifact is retained after the response.
- Run the targeted custody, delivery-contract, and report-export tests first, then the repository's normal formatting, lint, architecture, coverage-diff, and full two-lane test gates described in `AGENTS.md`. The new PDF template lands directly in front of `scripts/check_inline_styles.py` (CI runs it full-repo): every `<style>` element needs a `nonce` (`PDF_STYLE_EXCEPTIONS` applies only to Python scanning, not HTML), the `style=` detector is not tag-anchored so a literal `style="` anywhere in the file — including inside CSS text or a comment — fails, and the file must be git-tracked to be scanned. Also run `make typecheck` and `scripts/check_contract_policy.py`. No API schema regeneration is needed.

## Maintainer decisions (2026-08-11)

All open design questions are decided; nothing remains for later review rounds:

1. **Route shape:** new `custodyreceipt_export_pdf` route; the existing `custodyreceipt_export` URL stays JSON (backward compatible).
2. **E-mail transport API:** factor a recipient-aware helper from `core.events` (channel compatibility wrapper retained).
3. **Audit depth and visibility:** asset `JournalEntry` with requested/result state and **no receipt/session IDs** in the visible comment; correlation via `CustodyHandoffDelivery` (Decision B6) for authorized users.
4. **QR presentation:** behind the "Show QR code” toggle.
5. **Resend/idempotency policy:** per-session (3) and per-receipt (6) bounds plus per-operator cooldown, enforced through the durable delivery record (Decision B6); the durable model is part of this feature (not a separate change).
6. **Dependency premise:** reuse the already-approved Segno dependency; no new dependency. The #316 premise ("QR generation needs a small dependency") is factually wrong for this codebase and should be updated in the issue.

## Acceptance mapping

| Acceptance requirement | Design coverage |
|---|---|
| Primary native PDF download | Separate PDF route; split-button primary anchor with `hx-boost="false"` and `download` |
| Asset/holder/EULA/hash in PDF | Explicit allowlisted context and seven-part legal-record layout |
| JSON remains available unchanged | Existing route name, URL, payload, filename, headers, and tests frozen |
| PDF route returns `application/pdf`; 403/404 unchanged | Shared scoped lookup/permission contract plus dedicated route tests |
| QR for live handoff | Token-free, permission-checked SVG endpoint rendered with existing Segno |
| No token in request logs | QR/e-mail routes carry only receipt/session IDs; URL reconstructed server-side |
| E-mail reaches intended holder | Server derives only `receipt.holder.email`; no form/config/fallback audience |
| E-mail abuse bound | ≤3 attempts per signing session, ≤6 per receipt via `CustodyHandoffDelivery` row counts (session row lock, race-safe, durable) + short cache cooldown; refused attempts book no row |
| E-mail URL origin | Shared helper prefers `ITAMBOX_BASE_URL`, falls back to `build_absolute_uri`; copy/QR/e-mail inherit one origin |
| E-mail language | Holder's preference (or `LANGUAGE_CODE`), never the operator's active language |
| Graceful missing SMTP | Existing `DeliveryResult` terminal state reflected as disabled/safe UI |
| E-mail audit | Tenant-scoped asset journal records request and classified outcome without secrets or receipt/session IDs |
| CSP and i18n | Same-origin image, Bootstrap data attributes, no inline JS, translated labels/body |
| No migration/new dependency | Uses existing models, Segno, xhtml2pdf, and notification delivery implementation (plus one additive migration for Decision B6) |

## Scope addendum: fix the signature-request recipient fallback (maintainer-approved 2026-08-11)

A review finding during the design pass uncovered a pre-existing bug in the *other* custody e-mail path, `assets/services.py:182-231` (initial signature-request e-mail outside the signing-session flow): when the holder has no e-mail address, the recipient falls back to `email_config.test_recipient or email_config.from_address` — a bearer custody link is then mailed to the install-wide test/from address. This contradicts the recipient-binding principle this design enforces everywhere else. The maintainer approved fixing it in the same PR.

Fix contract:

- Remove the fallback. A holder without an e-mail address must produce a clear, safe error (`ValidationError` with a translatable message such as "The holder has no e-mail address") and **no** send; `test_recipient` and `from_address` are never recipients of custody links.
- The error surfaces through the existing caller's error path (no raw configuration values, addresses, or exception text in the UI).
- Tests: extend/adjust the existing signature-request tests (`assets/tests/test_assignments.py:99-104` and neighbours) to assert (a) a holder with an e-mail sends exactly to that address, (b) a holder without an e-mail raises the safe error without calling `send_mail`, and (c) `test_recipient`/`from_address` are never used even when populated.

Scope guard: only the recipient resolution in `assets/services.py` changes; the custody signing-session flow (Feature B) already derives the recipient exclusively from `receipt.holder.email` by design and needs no fallback removal.
