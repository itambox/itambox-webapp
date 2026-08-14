# CSP and inline-style policy

Issue #24 removes the application's dependency on CSP `style-src 'unsafe-inline'`.
The policy is intentionally fail-closed: browser HTML, server-generated HTML, and
authored frontend code must not quietly reintroduce inline styles.

## CSP contract

`CSPMiddleware` emits these style directives for browser requests:

- `style-src 'self' 'nonce-<request nonce>' https://rsms.me`
- `style-src-elem 'self' 'nonce-<request nonce>' https://rsms.me`
- `style-src-attr 'unsafe-inline'`

The nonce is request-local (`request.csp_nonce`) and is also exposed through the
CSP context variable while the request is rendering. There is no `unsafe-inline`
allowance in `style-src` or `style-src-elem`. Style elements in browser HTML therefore require
`nonce="{{ request.csp_nonce }}"` (or the equivalent generated nonce), while a
`style=` attribute is permitted at runtime. This narrowly scoped exception is
needed because HTMX and ApexCharts update element geometry through style
attributes. Authored templates, Python emitters, and frontend DOM style writes
remain prohibited by the blocking source gate. ApexCharts receives the request
nonce through its `chart.nonce` option for the style elements it creates.

HTMX's loading-indicator rules are authored in the application's bundled SCSS.
The base template sets `includeIndicatorStyles` to `false`, so HTMX does not
inject its runtime `<style>` element, which would be rejected by the nonce-only
`style-src-elem` policy. The global `.htmx-indicator` rules in `_layout.scss`
keep the page loader and filter indicators hidden until an HTMX request is
active, then show them with the expected transition. This keeps the loader
CSP-clean without a runtime style exception.

HTMX fragments are inserted into the already loaded document, so they must reuse
the parent document nonce. The base template publishes it in a meta element and
the HTMX bridge sends it as `X-CSP-Nonce`; `CSPMiddleware` accepts that header only
for `HX-Request` responses after strict grammar validation. The report designer's
preview `fetch` uses the same bridge before placing the response in iframe
`srcdoc`, which inherits the parent document policy.

## Source boundary

The blocking gate covers tracked production sources under `itambox/`:

- Django templates (`*.html`)
- Python HTML emitters, including `format_html()` and SVG/report fragments
- authored TypeScript/JavaScript DOM style APIs and generated `style=` markup

Tests, documentation, historical migrations, generated `static/dist/`, vendor
files, and locale data are not authored runtime emitters and are excluded from the
source inventory. Third-party bundles are not silently treated as authored policy
compliance; browser integration checks still need to exercise their runtime path
when CSP behavior changes. User-authored label content cannot be proven by a
source scan, so it is sanitized at the render boundary instead.

Run the gate from the repository root:

```bash
make inline-style-check
uv run --locked --group dev python scripts/check_inline_styles.py
```

The same check runs as the `inline-style` CI job and as a blocking pre-commit
hook. It fails when its inventory is empty, when it sees an authored inline attribute, an
un-nonced browser style element, a DOM style write, or `unsafe-inline` in
production source. The sole `unsafe-inline` exception is the exact
`style-src-attr` directive in `CSPMiddleware`; the gate continues to reject it in
`style-src` and `style-src-elem`.

## Browser regression check

After a CSP or bundled-library change, load each page with browser developer
tools open and preserve the console across HTMX navigation:

1. On the dashboard, confirm the Asset Status Labels donut and Asset Age
   Distribution bar chart have normal geometry, labels, and tooltips.
2. Open the report designer, generate a preview, and exercise its chart output.
3. Open the global asset scanner and a bulk/audit scanner page, then start and
   close the scanner overlay.
4. Confirm the console contains no CSP style violations from HTMX, ApexCharts,
   or the scanner library, and the Network panel contains no 404 requests for
   `tabler.min.css.map`, `tabler-vendors.min.css.map`, or
   `tom-select.bootstrap5.css.map`.
5. During an HTMX request, confirm the global top progress bar appears; after
   completion, confirm it hides again. Confirm there is no console CSP violation
   for an HTMX-injected style element.

## Dynamic styles

Static rules belong in authored SCSS/CSS. Genuine per-record values use the
helpers in `core/html_styles.py` and `core.templatetags.utility_tags`:

- color values are strict hexadecimal colors and are emitted as hash-based CSS
  classes with a request-nonce style rule;
- percentages are clamped to `0..100` before a width rule is emitted;
- lengths use a restricted unit/range grammar;
- label CSS is parsed with `tinycss2`, rewritten from `style=` attributes to
  generated classes, and limited to an explicit property/token allowlist.

No inline suppression comment is accepted. If a new dynamic use case appears,
add a bounded helper/policy test and update this document in the same change.

## HTML sinks and exceptions

The report custom HTML/Jinja override is a Beta, sandboxed opt-in surface on
`ReportTemplate`. It is available while `ITAMBOX_FEATURE_REPORT_DESIGNER` is
enabled, and migration-managed bounded grandfathered templates may continue
using it while the flag is disabled. The renderer limits output to the published
context with autoescaping and no model/object access; this prevents arbitrary
user report HTML from becoming a CSP exception.

Custom `LabelTemplate` Jinja remains a Beta print-layout feature. Before either
PDF sink, its output is rendered with an immutable, autoescaping sandbox over a
scalar asset DTO and passed through the label HTML/CSS sanitizer. Scripts,
event-handler attributes, unsafe tags, external resources, traversal paths, and
unapproved CSS are dropped. Only the internally generated barcode Data URI and
safe local `/static/` or `/media/` resources are accepted.

The authored label stylesheet carries both modern `break-after` and legacy
`page-break-after`: xhtml2pdf 0.2.x still needs the latter for deterministic
page boundaries. The single legacy-property exception is centralized in
`.stylelintrc.json`; it is not an inline suppression.

Three source-level style-element exceptions are centralized in
`scripts/check_inline_styles.py`:

1. `core/html_sanitizer.py` emits a sanitized style block through
   `sanitize_label_html_for_pdf()` only for the isolated xhtml2pdf sink.
2. `core/tasks/labels.py` builds self-contained HTML consumed by xhtml2pdf; it is
   not returned as browser HTML.
3. `core/reports/charts.py` emits a nonce-authorized chart style block only when
   an HTTP request nonce is active. Standalone report HTML/PDF receives the same
   authored rules from `polished_report.html`; the chart helper itself emits no
   naked no-nonce style element.

These exceptions are deliberately not available to templates or arbitrary
product code.
