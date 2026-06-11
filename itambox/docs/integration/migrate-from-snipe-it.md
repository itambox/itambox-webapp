# Migrate from Snipe-IT

This guide walks you through moving data from a Snipe-IT instance into ITAMbox
using the `import_snipeit` management command.

!!! warning "Run with `--dry-run` first"
    Always perform a dry run before your live import. A dry run fetches data from
    Snipe-IT, maps every entity, and reports counts without writing anything to
    the database.

## Prerequisites

- A running ITAMbox instance with at least one tenant created
- A Snipe-IT API token (read-only scope is sufficient)
- Network access from the ITAMbox host to the Snipe-IT API

### Create an API token in Snipe-IT

1. Log in to Snipe-IT as an administrator.
2. Go to **Admin → API Tokens**.
3. Click **Create New Token** and give it a descriptive name (e.g. `ITAMbox import`).
4. Copy the token — you will not be able to see it again.

**Export it as an environment variable before running the command:**

```bash
export SNIPEIT_TOKEN=your-token-here
```

!!! danger "Never pass the token as a CLI argument"
    Shell commands are logged in process lists and shell history.
    The `--token-env` argument takes the *name* of the environment variable,
    not the token itself.

## Basic usage

```bash
# Dry run — no data written
python manage.py import_snipeit \
  --url https://snipe.example \
  --token-env SNIPEIT_TOKEN \
  --tenant your-tenant-slug \
  --dry-run

# Live import
python manage.py import_snipeit \
  --url https://snipe.example \
  --token-env SNIPEIT_TOKEN \
  --tenant your-tenant-slug
```

## Options

| Flag | Description |
|---|---|
| `--url URL` | Snipe-IT base URL (no trailing slash). **Required.** |
| `--token-env VAR` | Name of the env var holding the API token. **Required.** |
| `--tenant SLUG` | Target ITAMbox tenant slug. Required unless `--map-companies-to-tenants` is set. |
| `--map-companies-to-tenants` | Create / match one ITAMbox tenant per Snipe-IT company (MSP mode). |
| `--update` | Re-sync fields on records that already exist (default: skip existing). |
| `--dry-run` | Fetch and map without writing to the database. |
| `--skip ENTITIES` | Comma-separated list of entity types to skip. Valid values: `assets`, `accessories`, `consumables`, `components`, `licenses`, `maintenances`. |
| `--admin-user USERNAME` | ITAMbox username to attribute the import to. Defaults to the first superuser. |

## Entity mapping

| Snipe-IT | ITAMbox | Notes |
|---|---|---|
| Status Labels | `StatusLabel` | Matched by name; existing labels (e.g. seeded defaults) are reused |
| Manufacturers | `Manufacturer` | Matched by name |
| Categories | `Category` | Matched by name; `category_type` maps to asset/accessory/component/consumable/license |
| Suppliers | `Supplier` | Matched by `snipeit_id` stored in `custom_field_data`, then by name |
| Locations | `Location` | Parent-child hierarchy is preserved; a shared "Imported (Snipe-IT)" site is created |
| Users | `AssetHolder` | Matched by `username` stored as `upn`; no Django auth user is created |
| Custom Fields | `CustomField` | `db_column_name` is normalised (strips `_snipeit_` prefix and trailing ID) |
| Fieldsets | `CustomFieldset` | Matched by name; fields are linked via M2M |
| Hardware Models | `AssetType` | Matched by name; linked to manufacturer, category, and fieldset |
| Hardware Assets | `Asset` | Matched by `snipeit_id`; active assignments create `AssetAssignment` |
| Accessories | `Accessory` + `AccessoryStock` | Checkout records create `AccessoryAssignment` |
| Consumables | `Consumable` + `ConsumableStock` | Quantity-in-stock only; individual consumptions are not imported |
| Components | `Component` + `ComponentStock` | Asset allocations create `ComponentAllocation` |
| Licenses | `License` + `LicenseSeatAssignment` | Software catalogue entries are created automatically |
| Maintenances | `AssetMaintenance` | Matched by `(asset, title, start_date)` tuple |

## Idempotency

Each entity is matched by a `snipeit_id` stored in `custom_field_data`.
Running the command a second time on the same data will skip all already-imported
records (or update them if `--update` is passed). No duplicates are created.

## MSP / multi-tenant mode

If your Snipe-IT instance uses **Companies**, you can map each company to a
separate ITAMbox tenant:

```bash
python manage.py import_snipeit \
  --url https://snipe.example \
  --token-env SNIPEIT_TOKEN \
  --map-companies-to-tenants
```

Tenants are matched by name. If a matching tenant already exists, assets are
imported into it; otherwise a new tenant is created.

## Out of scope (v1)

The following Snipe-IT data is **not** imported:

- File and image attachments
- Activity / audit history
- Depreciation schedules
- Kits
- Requestable-item requests
- LDAP / SAML sync configurations
- Custom reports

## Troubleshooting

**`Environment variable 'SNIPEIT_TOKEN' is empty or not set`**
Export the variable in the same shell session before running the command.

**`Tenant with slug '…' not found`**
Check the tenant slug in ITAMbox under **Organization → Tenants**.

**`Cannot connect to Snipe-IT at …`**
Verify the `--url` value and that the ITAMbox host can reach the Snipe-IT API.
The token needs at least read permission on the Snipe-IT API.

**Entities showing as `failed` in the summary**
Each entity failure is logged inline (e.g. `! location 42 'Berlin': …`).
Run with `--dry-run` and check the console output for error details.
