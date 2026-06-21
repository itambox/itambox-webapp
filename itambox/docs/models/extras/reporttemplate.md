# Report Templates

A **Report Template** defines a template format used to compile and export system data summaries.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Advanced Mode** | Enable custom Jinja2/HTML template code override. | Boolean | Yes |
| **Description** | Optional details. | Text | No |
| **Filter Tenants** | Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally. | Many-to-Many | No |
| **Group By Field** | Optional column key to group grid records under (e.g. location, status). | String | No |
| **Include Distribution Chart** | Toggle embedding spend or status distribution charts in the HTML report. | Boolean | Yes |
| **Include Summary Cards** | Toggle displaying top card widgets (totals, counts, financial sums). | Boolean | Yes |
| **Included Columns** | Checked columns to render in the report data grid. | JSON | No |
| **Name** | Unique name of the template. | String | Yes |
| **Report Type** | Catalog definition: `asset_summary`, `license_utilization`, `subscription_renewals`. | Choice | Yes |
| **Style Preset** | The style preset of the report template. | Choice | Yes |
| **Template Content** | Jinja2 HTML layout override structure. | Text | No |
| **Tenant** | The tenant owning this report template. Null represents system-wide templates. | Foreign Key | No |

## Security Guardrails

* **Sandboxed Execution**: Custom dynamic template layouts are compiled and executed strictly within a `jinja2.sandbox.SandboxedEnvironment` to prevent Remote Code Execution.
* **Syntax Compilation**: The `clean()` validation checks the Jinja2 code for syntax compilation issues before saving.
