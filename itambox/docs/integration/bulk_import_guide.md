# Bulk CSV & YAML Import Guide

## Introduction

ITAMbox supports importing resources in bulk using CSV (Comma-Separated Values), TSV (Tab-Separated Values), or YAML files. This feature allows you to efficiently create, update, and manage large numbers of records across all supported models. The import process handles relational field resolution, supports in-place updates (UPSERT), and processes imports asynchronously to ensure system stability.

## Format, Delimiters & Text Pasting

### Supported Formats
- **CSV** (Comma-Separated Values)
- **TSV** (Tab-Separated Values)
- **YAML** (YAML Ain't Markup Language)

### Input Methods
- **File Upload**: Upload a `.csv`, `.tsv`, or `.yaml` file directly.
- **Text Pasting**: Paste raw CSV, TSV, or YAML content directly into the import interface.

### Supported Delimiters (CSV/TSV)
| Delimiter | Symbol |
|-----------|--------|
| Comma     | `,`    |
| Semicolon | `;`    |
| Tab       | `\t`   |

### Encoding
- **UTF-8** (recommended)
- **UTF-8 with BOM** (Byte Order Mark)

> **Note**: Ensure your file is saved with the correct encoding to avoid character corruption, especially when using special characters or non-Latin scripts.

## Relational Field Resolution (Bilingual / Human-Friendly)

When importing data, relational fields (e.g., `site` on Location, `manufacturer` on AssetType) are resolved automatically. The system uses a three-tier lookup strategy to match values:

### Lookup Priority Order

1. **Primary Key (PK) Lookup** – Numeric ID
   - If the value is a numeric string (e.g., `"42"`), the system first attempts to find a record with that exact ID.
   - Example: `site: 42` → Looks for a Site with ID `42`.

2. **Exact Case-Sensitive Match**
   - If the value is not numeric, the system performs an exact case-sensitive match against the following fields (depending on the model):
     - `slug`
     - `name`
     - `model`
     - `username`
     - `upn`
   - Example: `manufacturer: "Dell Inc."` → Looks for a Manufacturer with `name` or `slug` exactly matching `"Dell Inc."`.

3. **Case-Insensitive Lookup**
   - If no exact match is found, the system performs a case-insensitive search across the same fields.
   - Example: `manufacturer: "dell inc."` → Matches `"Dell Inc."` if no exact match exists.

> **Important**: If no match is found after all three tiers, the import will fail for that row with a clear error message indicating the unresolved reference.

### Example: Resolving `site` on a Location

| Input Value | Lookup Result |
|-------------|---------------|
| `42`        | Site with ID `42` |
| `HQ-Office` | Site with `slug` = `"HQ-Office"` |
| `hq-office` | Site with `slug` = `"HQ-Office"` (case-insensitive fallback) |
| `Headquarters` | Site with `name` = `"Headquarters"` |

## In-place Updates (UPSERT)

The import system supports **UPSERT** (Update + Insert) functionality. By including the primary key column in your import file, you can update existing records instead of creating duplicates.

### How UPSERT Works

- **Include `id` column**: If you include the `id` column in your CSV/YAML and the value matches an existing record's ID, that record will be **updated** with the new values.
- **Exclude `id` column**: If you omit the `id` column, all rows will be treated as **new records** and inserted.
- **Primary Key Name**: For models where the primary key is not `id` (e.g., `username` for Asset Holder), use that field name instead.

### Example: Updating an Asset

```csv
id,name,asset_tag,serial_number,notes
101,Server-01,SRV-001,SN123456,Updated notes
```

This will update Asset with ID `101` with the new `name`, `asset_tag`, `serial_number`, and `notes`.

> **Note**: If the `id` value does not match any existing record, the row will be treated as a new insert.

## Asynchronous Background Processing

The import process follows a two-phase UI flow to ensure data integrity and provide real-time feedback.

### Phase 1: Preview

1. **Upload or Paste** your CSV/YAML data.
2. The system **validates** the format and parses the content.
3. A **preview table** displays the parsed rows with column headers.
4. Any **validation errors** (e.g., missing required fields, unresolved references) are highlighted.
5. You can review the data and make corrections before proceeding.

### Phase 2: Confirm & Process

1. Click **Confirm Import** to submit the data.
2. The system spawns an **asynchronous background task**.
3. A **Job** object is created to track the import progress.
4. You are automatically redirected to the **Job Detail view**, where you can monitor:
   - **Total rows** to process
   - **Rows processed** (completed)
   - **Rows succeeded** (successfully imported)
   - **Rows failed** (with error details)
   - **Current status** (Pending, Running, Completed, Failed)
5. The Job Detail view updates in **real-time** as the import progresses.

> **Tip**: You can navigate away from the Job Detail page and return later to check the status. The import continues in the background.

## Columns Supported per Model

### Asset

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | Asset name |
| `asset_tag` | No | String | Unique asset tag identifier (auto-generated from tag sequence if blank) |
| `serial_number` | No | String | Manufacturer serial number |
| `purchase_date` | No | Date | Date of purchase (YYYY-MM-DD) |
| `purchase_cost` | No | Decimal | Purchase cost/price |
| `order_number` | No | String | Purchase order number |
| `notes` | No | Text | Additional notes |

### Asset Type

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `manufacturer` | Yes | Relational | Manufacturer name, slug, or ID |
| `model` | Yes | String | Model name/number |
| `part_number` | No | String | Manufacturer part number |
| `category` | No | Relational | Asset type category |
| `asset_role` | No | Relational | Asset role |
| `description` | No | Text | Asset type description |
| `comments` | No | Text | Additional comments |

### Manufacturer

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | Manufacturer name |
| `slug` | No | String | URL-friendly identifier (auto-generated if empty) |
| `description` | No | Text | Manufacturer description |

### Location

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | Location name |
| `site` | Yes | Relational | Site name, slug, or ID |
| `slug` | No | String | URL-friendly identifier (auto-generated if empty) |
| `status` | No | String | Location status (e.g., Active, Inactive) |
| `parent` | No | Relational | Parent location name, slug, or ID |
| `facility` | No | String | Facility name |
| `description` | No | Text | Location description |

### Accessory

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | Accessory name |
| `manufacturer` | Yes | Relational | Manufacturer name, slug, or ID |
| `category` | No | String | Accessory category |
| `slug` | No | String | URL-friendly identifier (auto-generated if empty) |
| `part_number` | No | String | Manufacturer part number |
| `min_qty` | No | Integer | Minimum quantity threshold |
| `notes` | No | Text | Additional notes |

> **Note**: The `qty` (current quantity in stock) field is managed through the **AccessoryStock** model and is **not** directly importable on the catalog item. Use the AccessoryStock import path for stock-level operations.

### Consumable

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | Consumable name |
| `manufacturer` | Yes | Relational | Manufacturer name, slug, or ID |
| `category` | No | String | Consumable category |
| `slug` | No | String | URL-friendly identifier (auto-generated if empty) |
| `part_number` | No | String | Manufacturer part number |
| `min_qty` | No | Integer | Minimum quantity threshold |
| `notes` | No | Text | Additional notes |

> **Note**: The `qty` (current quantity in stock) field is managed through the **ConsumableStock** model and is **not** directly importable on the catalog item. Use the ConsumableStock import path for stock-level operations.

### License

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `name` | Yes | String | License name |
| `software` | Yes | String | Software name |
| `license_type` | No | String | Type of license (e.g., Perpetual, Subscription) |
| `product_key` | No | String | Product key or activation code |
| `seats` | No | Integer | Number of licensed seats/users |
| `purchase_date` | No | Date | Date of purchase (YYYY-MM-DD) |
| `purchase_cost` | No | Decimal | Purchase cost/price |
| `order_number` | No | String | Purchase order number |
| `expiration_date` | No | Date | License expiration date (YYYY-MM-DD) |
| `notes` | No | Text | Additional notes |

### Asset Holder

| Column | Required | Type | Description |
|--------|----------|------|-------------|
| `first_name` | Yes | String | First name |
| `last_name` | Yes | String | Last name |
| `upn` | Yes | String | User Principal Name (unique identifier) |
| `email` | No | String | Email address |
| `description` | No | Text | Asset holder description |
| `comments` | No | Text | Additional comments |

---

## Quick Reference: Required vs Optional Fields

| Model | Required Fields | Optional Fields |
|-------|----------------|-----------------|
| **Asset** | `name` | `asset_tag`, `serial_number`, `purchase_date`, `purchase_cost`, `order_number`, `notes` |
| **Asset Type** | `manufacturer`, `model` | `part_number`, `category`, `asset_role`, `description`, `comments` |
| **Manufacturer** | `name` | `slug`, `description` |
| **Location** | `name`, `site` | `slug`, `status`, `parent`, `facility`, `description` |
| **Accessory** | `name`, `manufacturer` | `category`, `slug`, `part_number`, `min_qty`, `notes` |
| **Consumable** | `name`, `manufacturer` | `category`, `slug`, `part_number`, `min_qty`, `notes` |
| **License** | `name`, `software` | `license_type`, `product_key`, `seats`, `purchase_date`, `purchase_cost`, `order_number`, `expiration_date`, `notes` |
| **Asset Holder** | `first_name`, `last_name`, `upn` | `email`, `description`, `comments` |

---

## Best Practices

1. **Use headers**: Always include column headers in the first row of your CSV/YAML file.
2. **Match column names exactly**: Column names are case-sensitive and must match the supported columns listed above.
3. **Clean your data**: Remove leading/trailing whitespace and ensure dates are in `YYYY-MM-DD` format.
4. **Use slugs for relational fields**: Slugs are URL-friendly and less prone to encoding issues than full names.
5. **Preview before confirming**: Always review the preview table to catch errors before starting the import.
6. **Monitor the Job**: After confirming, navigate to the Job Detail view to track progress and handle any failures.

---

## Dynamic Import Path for All Importable Models

Every model in ITAMbox that is not explicitly excluded can be imported via the generic import path:

```
/import/<app_label>/<model_name>/
```

For example:
- `/import/assets/asset/` — Assets
- `/import/assets/assettype/` — Asset Types
- `/import/assets/manufacturer/` — Manufacturers
- `/import/inventory/accessory/` — Accessories
- `/import/inventory/consumable/` — Consumables
- `/import/organization/location/` — Locations
- `/import/organization/assetholder/` — Asset Holders
- `/import/licenses/license/` — Licenses

This single view (`GenericObjectImportView`) serves **all** importable models — you do not need a per-model view. The curated import forms registered in `assets/forms/import_forms.py` provide domain-accurate field lists for each model automatically.

### Excluded Models

Some models are excluded from bulk import because they represent generated logs, system records, or complex configuration managed exclusively through the UI or API. These are defined in `IMPORT_EXCLUDED_MODELS` (in `core/forms/import_forms.py`) and return a **404** if accessed via the generic import path:

| App | Excluded Models |
|-----|----------------|
| `core` | `objectchange`, `notification`, `job` |
| `extras` | `alertlog`, `event`, `journalentry`, `alertrule`, `notificationchannel`, `scheduledreport`, `reporttemplate`, `eventrule`, `webhookendpoint`, `dashboard` |
| `organization` | `membership`, `role`, `rolegrant`, `rolegrantscope`, `tenantresourcegrant` |
| `users` | `groupmembership`, `token`, `user`, `usergroup` |

> **Rule of thumb**: If a model is **not** in the table above, it is importable via `/import/<app_label>/<model_name>/`.
