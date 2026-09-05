# Report Templates

A **Report Template** defines the visual configuration used to compile and export system data summaries. The built-in renderer is used by default for on-demand and scheduled reports. Custom HTML/Jinja is a Beta, sandboxed opt-in surface: it is available when the report-designer flag is enabled, and migration-managed bounded grandfathered templates may continue using it while the flag is disabled.

## Fields

### Description

Optional details.

### Filter Tenants

Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally.

### Group By Field

Optional column key to group grid records under (e.g. location, status).

### Include Distribution Chart

Toggle embedding spend or status distribution charts in the HTML report.

**Required:** Yes.

### Include Summary Cards

Toggle displaying top card widgets (totals, counts, financial sums).

**Required:** Yes.

### Included Columns

Checked columns to render in the report data grid.

### Name

Unique name of the template.

**Required:** Yes.

### Report Type

Which data set to compile. One of: `asset_summary`, `license_utilization`, `subscription_renewals`, `asset_maintenance`, `asset_depreciation`, `software_inventory`, `contract_renewals`, `warranty_expiration`, `asset_disposal_eol`, `hardware_inventory`, `custody_compliance`.

**Required:** Yes.

### Style Preset

Visual layout for the HTML/PDF render. One of: `default`, `compact`, `financial`, `minimal`.

**Required:** Yes.

### Tenant

The tenant owning this report template. Null represents system-wide templates.


## Security Guardrails

* **Built-in renderer and sandboxed opt-in**: Normal templates use the versioned system template and style presets. Custom HTML/Jinja is limited to the published context and sandbox, with autoescaping and no model/object access; it is available only while the operator flag is enabled or for migration-managed bounded grandfathered templates. Grandfathered templates are read-only while the flag is disabled.
* **Tenant scoping**: Report data is compiled under the active tenant and configured filter-tenant constellation.
