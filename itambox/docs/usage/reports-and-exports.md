# Reports & Exports

ITAMbox provides a flexible reporting and export system for getting data out
of the platform — whether that's a one-off CSV export of your asset inventory,
a scheduled PDF report delivered by email every Monday morning, or printed
QR-code labels for your server rack.

---

## Export Templates

**Export Templates** define custom downloadable formats for any model's list
data. Instead of being limited to the default CSV export, you can create
templates that produce JSON, XML, custom CSV layouts, or any text-based format.

### Creating an Export Template

Navigate to **Extras → Export Templates** and click **Add**:

| Attribute | Description |
|-----------|-------------|
| **Name** | Unique identifier (e.g. "Asset CSV for Finance") |
| **Content Type** | The model this template exports (e.g. `assets \| asset`) |
| **Template Code** | Jinja2 template rendered over the full query result set |
| **MIME Type** | HTTP Content-Type header (e.g. `text/csv`, `application/json`) |
| **File Extension** | Download filename extension (e.g. `csv`, `json`, `xml`) |
| **Description** | Optional notes on what the template produces |
| **Download as attachment** | Serve as file download (default) or display inline in browser |

### Template Code

The template receives a single variable — `queryset` — containing the full,
filtered result set. The template author is responsible for iterating rows and
emitting any header:

```jinja2
{# CSV Export Template #}
Asset Tag,Name,Status,Purchase Date
{% for asset in queryset %}
"{{ asset.asset_tag }}","{{ asset.name }}","{{ asset.status }}","{{ asset.purchase_date }}"
{% endfor %}
```

For JSON exports:

```jinja2
[
{% for obj in queryset %}
  {
    "id": {{ obj.id }},
    "tag": "{{ obj.asset_tag }}",
    "name": "{{ obj.name }}"
  }{% if not loop.last %},{% endif %}
{% endfor %}
]
```

> [!IMPORTANT]
> Export templates are rendered in a **sandboxed Jinja2 environment**.
> Dangerous filters (`|attr`, `|format`, `|map`, `|pprint`) and globals
> (`cycler`, `joiner`, `namespace`, `lipsum`) are disabled. Sensitive Python
> dunder attributes are blocked. This is defence-in-depth — only superusers
> can author templates, but the sandbox prevents accidental or malicious
> server-side code execution.

### Security: CSV Formula Injection

Use the built-in `|csv_safe` filter to neutralise spreadsheet formula injection
when exporting to CSV/Excel formats. Wrap any field that might start with `=`,
`+`, `-`, or `@`:

```jinja2
"{{ asset.name|csv_safe }}","{{ asset.notes|csv_safe }}"
```

---

## Label Templates

**Label Templates** define the printable layout for physical asset tags,
barcodes, and QR code labels. They control both the label dimensions and the
content printed on each label.

### Creating a Label Template

Navigate to **Extras → Label Templates** and click **Add**:

| Attribute | Description |
|-----------|-------------|
| **Name** | Label layout name (e.g. "Avery 5160 — Asset Tags") |
| **Page Width** | Label width in inches (e.g. `2.25`) |
| **Page Height** | Label height in inches (e.g. `1.25`) |
| **Barcode Format** | Symbology for the printed code |
| **Template Code** | Jinja2/HTML template for label content and layout |
| **Description** | Optional printer/stock compatibility notes |

### Supported Barcode Formats

| Format | Slug | Best For |
|--------|------|----------|
| **Code 128** | `code128` | General-purpose asset tags — dense, supports alphanumeric, widely supported by scanners |
| **Code 39** | `code39` | Legacy systems, simple alphanumeric, lower density |
| **QR Code** | `qr` | Mobile scanning, URLs, large data payloads (up to ~4K chars) |
| **Data Matrix** | `datamatrix` | Small labels, industrial/PCB marking, high data density in small footprint |

### Printing Labels

1. Navigate to the asset list view.
2. Select the assets you want to label (checkbox selection).
3. Click the **Labels** action button in the list toolbar.
4. Choose a **Label Template** from the dropdown.
5. ITAMbox generates a print sheet with one label per selected asset.
6. Use your browser's print dialog (Ctrl+P / Cmd+P) to print at 100% scale.

> [!WARNING]
> Set your browser print settings to **no margins** and **100% scale**.
> Page scaling, "fit to page", or default browser margins will misalign
> labels on the physical sticker sheet. Always test-print on plain paper
> first.

### Template Code Variables

Label templates have access to asset properties including:

- `{{ asset.asset_tag }}` — the unique asset tag (encoded in the barcode)
- `{{ asset.name }}` — asset display name
- `{{ asset.serial_number }}` — manufacturer serial number
- `{{ asset.asset_type.name }}` — the asset type / model name
- `{{ asset.location.name }}` — current location

---

## Report Templates

**Report Templates** define the content, layout, and styling of compiled
system reports. They are used both for on-demand report generation and as
the basis for scheduled reports.

### Creating a Report Template

Navigate to **Extras → Report Templates** and click **Add**:

| Attribute | Description |
|-----------|-------------|
| **Name** | Unique template name |
| **Report Type** | The data set to compile (see below) |
| **Included Columns** | Checked columns rendered in the report data grid |
| **Include Summary Cards** | Show/hide top-level KPI cards (totals, counts, sums) |
| **Include Distribution Chart** | Embed a distribution chart in the HTML report |
| **Group By Field** | Optional column to group grid rows under (e.g. `location`, `status`) |
| **Style Preset** | Visual layout for HTML/PDF renders |
| **Filter Tenants** | Limit data to selected tenants (blank = global aggregate) |
| **Advanced Mode** | Enable custom Jinja2/HTML template override |
| **Template Content** | Custom Jinja2 HTML layout (only when Advanced Mode is on) |
| **Description** | Optional notes |

### Available Report Types

| Report Type | Slug | What It Contains |
|-------------|------|-----------------|
| Asset Inventory Summary | `asset_summary` | Full asset inventory with status, location, financials |
| License Utilization | `license_utilization` | License seats purchased vs assigned, compliance gaps |
| Subscription Renewals | `subscription_renewals` | Upcoming subscription expirations, costs, renewal contacts |
| Asset Maintenance & Repairs | `asset_maintenance` | Maintenance history, open repair tickets, costs |
| Asset Depreciation Summary | `asset_depreciation` | Book values, depreciation schedules, GWG write-offs |
| Software Catalog & Installations | `software_inventory` | Installed software, versions, licensing status |
| Contract Renewals & Expirations | `contract_renewals` | Vendor contracts nearing expiry, value, auto-renewal flags |
| Warranty Expiration | `warranty_expiration` | Assets with warranties expiring in configurable windows |
| Asset Disposal & End-of-Life | `asset_disposal_eol` | Disposed assets, WEEE compliance, data sanitization records |
| Hardware Inventory | `hardware_inventory` | Accessories, consumables, components, stock levels |
| Custody & EULA Compliance | `custody_compliance` | Asset custody sign-offs, EULA acceptance tracking |

### Style Presets

| Preset | Slug | Appearance |
|--------|------|-----------|
| **Executive (Branded)** | `default` | Indigo brand band, accented summary cards — for leadership |
| **Compact (Dense)** | `compact` | Dense rows with zebra striping — for audit and operations lists |
| **Financial (Ledger)** | `financial` | Stone ledger with emphasised monetary totals and tabular figures |
| **Minimal (Clean)** | `minimal` | Clean black-on-white, single indigo hairline — for forwarding, embedding, or printing |

### Custom Layouts (Advanced Mode)

When **Advanced Mode** is enabled, you provide a full Jinja2 HTML template
in the **Template Content** field. The template has access to the compiled
report data and can completely override the generated output:

```jinja2
<!DOCTYPE html>
<html>
<head><title>{{ report.name }}</title></head>
<body>
  <h1>{{ report.name }}</h1>
  <table>
    {% for row in data %}
    <tr>
      <td>{{ row.asset_tag }}</td>
      <td>{{ row.status }}</td>
      <td>{{ row.book_value|floatformat:2 }} €</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
```

> [!WARNING]
> Advanced Mode templates bypass the built-in layout engine entirely.
> You are responsible for the full HTML document, styling, and responsive
> behaviour. Invalid Jinja2 syntax is caught during template save validation.

---

## Scheduled Reports

**Scheduled Reports** automatically compile a Report Template on a recurring
schedule and deliver the result via email or notification channels.

### Creating a Scheduled Report

Navigate to **Extras → Scheduled Reports** and click **Add**:

| Attribute | Description |
|-----------|-------------|
| **Name** | Display name for this schedule |
| **Report** | The Report Template to compile |
| **Frequency** | How often to run (see below) |
| **Cron Expression** | Custom cron string (only when Frequency = Custom Cron) |
| **Start Time** | Time of day to execute (e.g. `08:00:00`) |
| **Format** | Delivery format |
| **Recipients** | Comma-separated email addresses |
| **Channels** | Notification channels to deliver through (optional) |
| **Save To Archive** | Keep a copy of each generated report |
| **Filter Tenants** | Scope report data to specific tenants |
| **Is Active** | Enable/disable this schedule |

### Frequencies

| Frequency | When It Runs |
|-----------|-------------|
| **Once** | Single execution at the next Start Time |
| **Hourly** | Every hour |
| **Daily** | Every day at Start Time |
| **Weekly** | Every week on the same day |
| **Biweekly** | Every two weeks |
| **Monthly** | Once per month |
| **Quarterly** | Every three months |
| **Yearly** | Once per year |
| **Custom Cron** | Arbitrary cron expression (e.g. `0 8 * * 1-5` for weekdays at 8 AM) |

### Delivery Formats

| Format | Slug | Delivery Method |
|--------|------|----------------|
| **HTML Email** | `html` | Rendered report inline in the email body |
| **CSV Attachment** | `csv` | CSV file attached to the email |
| **PDF Attachment** | `pdf` | PDF rendered via xhtml2pdf, attached to the email |
| **Excel (XLSX) Attachment** | `xlsx` | Excel workbook via openpyxl, attached to the email |

### Notification Channels

In addition to email delivery via the **Recipients** field, scheduled reports
can be attached to one or more **Notification Channels** (configured under
Extras → Notification Channels). This enables delivery to webhooks, Slack,
Microsoft Teams, or other integrated platforms.

### Monitoring

Each scheduled report tracks execution state:

| Attribute | Description |
|-----------|-------------|
| **Last Run** | Timestamp of the most recent execution |
| **Last Status** | `success` or `failed` — check this after the first run to confirm delivery |

When a Scheduled Report is deleted, its linked background task schedule is
automatically cleaned up to prevent orphaned cron jobs.

---

## Saved Filters

**Saved Filters** let you capture and reuse list view filter configurations.
Instead of re-entering the same filters every time you view the asset list,
save them once and apply them with a single click.

### Saving a Filter

1. Navigate to any list view (e.g. Assets, Licenses, Subscriptions).
2. Apply your desired filters using the filter panel.
3. Click the **Save Filter** button (bookmark icon) in the filter bar.
4. Give the filter a **Name** and optional **Description**.
5. Choose whether to **Share** it (visible to all tenant members) or keep it
   private.

> [!TIP]
> Saved Filters store the query parameters, not the result set. Applying a
> saved filter re-runs the query against the current database state, so
> results are always up-to-date.

### Applying a Saved Filter

From any list view:

1. Click the **Saved Filters** dropdown in the filter bar.
2. Select a filter from the list.
3. The list view reloads with the saved filter parameters applied.

The active filter name is displayed in the filter bar, and you can clear it
by clicking the **Reset** button.

### Managing Saved Filters

Navigate to **Extras → Saved Filters** to:

- **Edit** a filter's name, description, or sharing setting
- **Disable** a filter without deleting it — it disappears from the dropdown
- **Delete** filters that are no longer needed

### Filter Scope

| Scope | Visibility |
|-------|-----------|
| **Shared** (default) | Visible to all members of the owning tenant |
| **Private** (`shared` unchecked) | Visible only to the creator |
| **System-wide** (`tenant` is null) | Visible across all tenants — superusers only |

---

## The Export Workflow

The standard export workflow — from list view to downloaded file — works as
follows:

1. **Navigate** to a list view (e.g. **Assets**, **Licenses**, **Subscriptions**).
2. **Filter** the list to the subset of records you want to export. Use the
   filter panel, saved filters, or search to narrow the result set.
3. **Click Export** — the export button (download icon) is in the list view
   toolbar.
4. **Select an Export Template** from the dropdown. The default "CSV Export"
   template is always available; custom templates you've created appear below.
5. **Choose a file format** if prompted — some templates offer format options
   (CSV, JSON).
6. **Download** — the file is rendered server-side and served as a browser
   download.

> [!IMPORTANT]
> Exports respect the **current filter state** of the list view. Only rows
> that match the active filters are included in the export. To export the
> full dataset, clear all filters before clicking Export.

### Export File Naming

Downloaded files are automatically named using the pattern
`{model}_export.{extension}` — for example, `asset_export.csv` or
`license_export.json`. The filename is ASCII-safe to work across all
operating systems.

---

## Troubleshooting

### Export / Report Issues

**Export is empty or missing columns**
: Verify your active list filters are not too restrictive. Check that
  the export template's **Content Type** matches the model you are exporting.
  For report templates, ensure the **Included Columns** list has the columns
  you expect checked.

**"Jinja2 template compilation failed" when saving a Report Template**
: Your **Template Content** (Advanced Mode) contains invalid Jinja2 syntax.
  Common causes: unmatched `{% %}` / `{{ }}` tags, undefined variables in
  control flow blocks, or unclosed HTML tags. Validate the template syntax
  before saving.

**Label print is misaligned or scaled wrong**
: Check browser print settings — margins must be **None**, scale must be
  **100%**. Also verify the label template's **Page Width** and **Page Height**
  match your physical label sheet dimensions exactly.

### Scheduled Report Issues

**Report never runs**
: Check that **Is Active** is toggled on. Verify the `django-q2` cluster is
  running (check the Django admin Q cluster page). Ensure the **Start Time**
  is in the future and the worker is not backlogged.

**"Cron expression is required" validation error**
: When **Frequency** is set to **Custom Cron**, the **Cron Expression** field
  cannot be blank. Provide a valid 5-field cron expression (e.g. `0 9 * * 1`
  for every Monday at 9 AM).

**"Invalid Cron expression" validation error**
: The cron string does not parse. Verify format: `minute hour day month weekday`
  (5 fields, space-separated). Use a tool like [crontab.guru](https://crontab.guru)
  to validate your expression.

**"Not a valid email address" validation error**
: One or more addresses in the **Recipients** field fail email format
  validation. Check for typos, missing `@` signs, or trailing commas.
  Addresses must be comma-separated: `alice@example.com, bob@example.com`.

**Report delivered but empty or wrong data**
: Check the **Filter Tenants** setting — if specific tenants are selected,
  only data from those tenants is included. Verify the underlying Report
  Template has the correct **Report Type** and **Included Columns**.

### Saved Filter Issues

**Saved filter dropdown is empty**
: All saved filters are either disabled or scoped to another tenant. Check
  Extras → Saved Filters — ensure at least one filter is **Enabled** and
  matches the current tenant context.

**"Filter already exists" when saving**
: A saved filter with the same name already exists for this model type and
  tenant. Choose a different name or delete/rename the existing filter.
