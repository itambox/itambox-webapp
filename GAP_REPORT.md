# ITAMbox Documentation Gap Analysis

> Generated 2026-07-20 by automated comparison of `itambox/docs/` against the actual codebase.

## Total findings: 26 gaps (4 HIGH, 10 MEDIUM, 12 LOW)

---

## 🔴 HIGH Severity

### 1. Index module coverage: 5 documented vs 12 actual apps
**Files:** `docs/index.md` vs `*/apps.py` (12 apps)

The landing page lists 5 "Key Operational Modules":
1. Organization
2. Physical Assets
3. Inventory & Stock
4. Software & SaaS
5. Operations

The codebase has **12 Django apps** with their own `apps.py`:
`assets`, `compliance`, `core`, `extras`, `inventory`, `licenses`, `organization`, `procurement`, `software`, `subscriptions`, `users` (+ `itambox` project config)

**7 apps invisible from the landing page:** `compliance`, `extras`, `licenses`, `procurement`, `software`, `subscriptions`, `users`

Notably, `docs/models/` has model reference pages for ALL of these, but the user-facing index doesn't acknowledge them.

---

### 2. Asset model field requirement mismatches (4 fields)
**File:** `docs/models/assets/asset.md` vs `assets/models/asset.py`

The doc marks these fields as **Required** but they're `null=True, blank=True` in code:
| Field | Doc says | Code says | Reason |
|-------|---------|-----------|--------|
| `asset_tag` | Required | Optional | auto-generated via `AssetTagSequence` if blank |
| `asset_type` | Required (`ForeignKey`) | Optional (`null=True`) | assets can exist before type catalog is ready |
| `status` | Required | Optional (`null=True`) | falls back to default `StatusLabel` |
| `requestable` | Required | Has `default=True` | auto-default, API users can omit |

---

### 3. Entire feature areas have zero user-facing documentation
16 feature areas exist in code and are navigable in the UI, but have **no** files under `docs/usage/` or `docs/operations/`:

| Feature | App | Has model doc? | Has usage guide? |
|---------|-----|---------------|-----------------|
| Alert Rules & Notifications | `extras` | ✅ | ❌ |
| Webhooks & Event Rules | `extras` | ✅ | ❌ |
| Custom Fields & Fieldsets | `extras` | ✅ | ❌ |
| Scheduled Reports | `extras` | ✅ | ❌ |
| Export Templates | `extras` | ✅ | ❌ |
| Label Templates | `extras` | ✅ | ❌ |
| Contracts (procurement) | `procurement` | ✅ | ❌ |
| Purchase Orders | `procurement` | ✅ | ❌ |
| Software Catalog | `software` | ✅ | ❌ |
| Asset Maintenance | `assets/models/maintenance.py` | ✅ | ❌ |
| Asset Requests | `assets` | ✅ | ❌ |
| Asset Reservations | `assets` | ✅ | ❌ |
| Kits | `inventory` | ✅ | ❌ |
| Cost Centers | `organization` | ✅ | ❌ |
| SSO Configuration (LDAP/SAML/OIDC) | `core/auth/` | ❌ | ❌ |
| API Tokens & RBAC | `users` | ❌ | ❌ |

---

### 4. ~40 undocumented environment variables
**Files:** `docs/operations/installation.md` (7 vars) vs `.env.example` (~50 vars)

Installation.md documents: `SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`, `API_TOKEN_PEPPERS`, `DB_*`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `EMAIL_*`

Completely undocumented in any user-facing doc:
| Var | What it does |
|-----|-------------|
| `ITAMBOX_BASE_URL` | Public URL for QR code labels & outbound links |
| `ITAMBOX_DEFAULT_CURRENCY` | Fallback ISO 4217 currency |
| `ITAMBOX_PAGINATOR_COUNT_CAP` | List row counter bound |
| `ITAMBOX_SESSION_COOKIE_AGE` | Session lifetime |
| `ITAMBOX_DOCS_ROOT` | MkDocs output path override |
| `ITAMBOX_REQUIRE_MFA` | Enforce TOTP for superusers |
| `ITAMBOX_REQUIRE_CUSTODY_SIGNIN` | Custody receipt signature toggle |
| `ITAMBOX_ALLOW_GLOBAL_CUSTODY_TEMPLATES` | Cross-tenant custody templates |
| `ITAMBOX_TENANT_LDAP_CONFIGS` | Multi-tenant LDAP SSO |
| `ITAMBOX_TENANT_SAML_CONFIGS` | Multi-tenant SAML SSO |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | Multi-tenant OIDC SSO |
| `ITAMBOX_TENANT_INTUNE_CONFIGS` | Intune discovery (noted in discovery-sync.md) |
| `ITAMBOX_PLUGINS` | Plugin registration |
| `ITAMBOX_LOG_LEVEL` | Logging |
| `ITAMBOX_CACHE_BACKEND` / `ITAMBOX_REDIS_URL` | Cache backends |
| `ITAMBOX_DB_SSLMODE` | Database SSL mode |
| `ITAMBOX_DB_CONN_MAX_AGE` | DB connection pooling |
| `ITAMBOX_EMAIL_TIMEOUT` | SMTP timeout |
| `ITAMBOX_SECURE_SSL_REDIRECT` | HTTPS redirect |
| `ITAMBOX_HSTS_*` | HSTS headers (3 vars) |
| `ITAMBOX_STATIC_ROOT` / `ITAMBOX_MEDIA_ROOT` | Static/media overrides |
| `ITAMBOX_CORS_*` | CORS configuration (2 vars) |
| `RATELIMIT_CACHE` | Rate limiter cache alias |
| `ITAMBOX_SERVER_EMAIL` | Server error from-address |
| `ITAMBOX_CHANGELOG_RETENTION_DAYS` | Data retention (4 vars, noted in data-retention.md) |

---

## 🟡 MEDIUM Severity

### 5. API docs missing 4 base paths + 5 "..." placeholders
**Files:** `docs/integration/developer_guide.md` vs `itambox/api/urls.py`

The developer guide lists these API paths but **entirely omits**:
- `GET /api/` (API root)
- `GET /api/status/`
- `GET /api/auth-check/`
- `GET /api/core/object-changes/`
- `GET/POST /api/extras/...`
- `GET/POST /api/procurement/...`
- `GET/POST /api/plugins/...`
- `GET/POST /api/tenants/<slug>/scim/v2/`
- `GET/POST /api/providers/<slug>/scim/v2/`

Additionally, 5 modules use `...` placeholder in the endpoint table instead of listing actual paths:
- `/api/compliance/...`
- `/api/inventory/...`
- `/api/licenses/...`
- `/api/software/...`
- `/api/subscriptions/...`

The `organization` section only lists sites, locations, and asset-holders — missing: regions, tenants, cost centers, site groups, tenant groups.

---

### 6. Bulk import: doc documents only 8 of potentially 30+ importable models
**Files:** `docs/integration/bulk_import_guide.md` vs `core/forms/import_forms.py`

The guide documents 8 curated import forms (Asset, AssetType, Manufacturer, Location, Accessory, Consumable, License, AssetHolder).

The actual import system has two paths:
1. **8 curated forms** (matching docs)
2. **Dynamic import** via `GenericObjectImportView` — ANY model not in `IMPORT_EXCLUDED_MODELS` is importable at `/import/<app_label>/<model_name>/`

`IMPORT_EXCLUDED_MODELS` (from `core/forms/import_forms.py:35-56`) explicitly excludes only:
```
core.objectchange, core.notification, core.job,
extras.alertlog, extras.event, extras.journalentry,
extras.alertrule, extras.notificationchannel, extras.scheduledreport, extras.reporttemplate,
extras.eventrule, extras.webhookendpoint, extras.dashboard,
organization.membership, organization.role, organization.rolegrant,
organization.rolegrantscope, organization.tenantresourcegrant,
users.groupmembership, users.token, users.user, users.usergroup
```

Everything else is importable — ~30 additional models users don't know they can import.

---

### 7. Bulk import - Asset field naming errors
**File:** `docs/integration/bulk_import_guide.md:119` vs `assets/models/asset.py`

| Doc column | Actual model field |
|-----------|-------------------|
| `description` | `notes` (description doesn't exist on Asset) |
| `asset_tag` (Required) | `asset_tag` (Optional — auto-generated) |

The `notes` field is listed separately in docs as optional but `description` is documented as an import column that doesn't exist.

---

### 8. Bulk import - Accessory/Consumable `qty` misleading
**Files:** `docs/integration/bulk_import_guide.md:167,181` vs `inventory/models.py:193,265`

Docs list `qty` as a column on Accessory and Consumable imports. In code, `qty` lives on `AccessoryStock` / `ConsumableStock` models, NOT on the catalog `Accessory` / `Consumable` models. The import forms for the catalog items don't include `qty` — users would get an error.

---

### 9. Bulk import - AssetType accepts undocumented columns
**File:** `assets/forms/import_forms.py:27-32`

The `AssetTypeBulkImportForm` accepts `category` and `asset_role` columns that are absent from the guide. The guide only lists `manufacturer`, `model`, `part_number`, `description`, `comments`.

---

### 10. 10 undocumented management commands
**Files:** `core/management/commands/*.py` (13 commands)

**Documented:** `sync_intune`, `import_snipeit`, `prune_changelog`, `offboard_user` (script, not a command), `seed_data` (mentioned)

**Undocumented:**
| Command | What it does |
|---------|-------------|
| `compile_locales` | Compiles translation `.po` files to `.mo` |
| `export_datamodel` | Exports data model to DOT/SVG (used to generate docs) |
| `integrity_report` | Checks database integrity constraints |
| `list_failed_tasks` | Lists failed django-q2 background tasks |
| `purge_deleted` | Hard-deletes rows past their soft-delete retention |
| `rotate_encryption_keys` | Rotates `FIELD_ENCRYPTION_KEYS` |
| `run_jobs` | Runs queued background jobs manually |
| `sync_tenant_ldap` | Syncs LDAP directory with ITAMbox users |
| `validate_role_permissions` | Validates role-permission mappings |

Note: `offboard_user` is a Python script (`docs/integration/offboard_user.py`), not a management command. It's documented correctly but shelved outside the command namespace.

---

### 11. Tenant model missing documented fields
**File:** `docs/models/organization/tenant.md` vs `organization/models.py:220`

Fields in code but absent from docs:
- `is_provider` (Boolean) — marks this tenant as a service provider
- `managed_by` (FK to self) — parent provider tenant
- `changelog_retention_days` (Integer) — per-tenant retention override (documented in `data-retention.md` but not model ref)

---

### 12. AssetHolder `user` field type error
**File:** `docs/models/organization/assetholder.md:15` vs `organization/models.py:377`

Doc says: `User` — Optional **OneToOne** link to Django user
Code says: `ForeignKey(User, ...)` — a user can be linked to multiple AssetHolders

---

### 13. Environment var format drift in discovery-sync.md
**File:** `docs/integration/discovery-sync.md:29`

The documented Intune config uses **flat keys**:
```json
{"acme": {"azure_tenant_id": "...", "client_id": "...", "client_secret": "..."}}
```

But `.env.example:175` uses **UPPER_SNAKE_CASE** keys:
```json
{"tenant-alpha": {"TENANT_ID": "...", "CLIENT_ID": "...", "CLIENT_SECRET": "..."}}
```

These don't match. Users copying one format into the other would get a silent config mismatch.

---

### 14. Plugin docs mention `core/settings/base.py` but actual config is env-var driven
**File:** `docs/plugins/getting_started.md:80`

Doc says: "add its package name to the `PLUGINS` list in `core/settings/base.py`"

But `.env.example` shows the configurable path: `ITAMBOX_PLUGINS=itambox_esign` (comma-separated env var). Editing `base.py` directly is fragile and gets overwritten on update. The docs should point to env var configuration.

---

## 🟢 LOW Severity

### 15. Scanning URL mismatch
**File:** `docs/usage/scanning.md:11` vs `core/urls.py:76`

Doc references: `/scan/`
Actual URL: `/scan/resolve/`

---

### 16. 7 extras models have no doc pages
**Directory:** `docs/models/extras/` vs `extras/models.py`

Models in code with NO corresponding `.md` file:
- `Event` — event log entries
- `JournalEntry` — timeline notes
- `Bookmark` — user bookmarks
- `ConfigRevision` — configuration change revisions
- `ImageAttachment` — image uploads
- `FileAttachment` — file attachments
- `Dashboard` — dashboard configuration

These all have doc pages for other models in the same app (alertlog, alertrule, customfield, etc.) but not these.

---

### 17. Docs directory naming inconsistency
**Directories:** `docs/models/auth/` vs `docs/models/users/`

The module is `users` but model docs are under `docs/models/auth/` (containing only `user.md`). Other modules follow the app name convention (e.g., `docs/models/assets/`, `docs/models/organization/`).

---

### 18. Compliance model doc missing
**Directory:** `docs/models/compliance/`

`CustodyTemplate.md` updated 2026-07-20 but `AuditSession` model has no doc page. The `docs/models/compliance/` dir has `assetmaintenance.md`, `custodyreceipt.md`, `custodytemplate.md` — missing `AssetAudit` and `AuditSession`.

---

### 19. Dashboard doc refs `Save Layout` button — not verified
**File:** `docs/dashboard.md:45`

Doc says: "Click the **Save Layout** button in the toolbar to persist your custom positions." Not verified whether a dedicated Save button exists in the current UI or if layout auto-saves on unlock/lock.

---

### 20. Backup script uses `POSTGRES_USER` / `POSTGRES_DB` but .env uses `ITAMBOX_DB_*`
**File:** `docs/operations/backup-restore.md:56` vs `.env.example:77-80`

Backup doc: `pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"`
.env vars: `ITAMBOX_DB_NAME=itambox`, `ITAMBOX_DB_USER=itambox`

The Compose file maps these differently; the backup script using `POSTGRES_*` vars only works if the user's Compose setup passes them through. A note is warranted.

---

### 21. Depreciation doc field missing from asset model doc
**File:** `docs/usage/depreciation.md` documents `Asset.in_service_date`, `Asset.disposed_at`, `Asset.disposal_value`

`docs/models/assets/asset.md` lists `In Service Date`, `Disposal Value`, `Disposed At` as fields (consistent). ✅ No gap here — cross-reference is fine.

---

### 22. Release checklist references `CONTRIBUTING.md` gates
**File:** `docs/development/release-checklist.md:6`

"canonical gates under 'Run the checks' in the repository-root `CONTRIBUTING.md` pass, including lint, smoke, full tests, and Playwright." The documented gate list should match the actual CI config.

---

### 23. Data retention doc mentions `prune_changelog` auto-schedule
**File:** `docs/operations/data-retention.md:66`

Claims the prune runs "automatically once a day" via django-q2 schedule. No user-facing doc explains how to verify this schedule is active, pause it, or what happens when qcluster is down.

---

### 24. SCIM doc says provider filtering "overstated" in ServiceProviderConfig
**File:** `docs/integration/scim.md:71`

"`ServiceProviderConfig` currently overstates provider filtering." This is a known bug documented in the doc itself — the doc is accurate but the code disagrees (by design). Worth tracking as a separate issue.

---

### 25. `itambox_esign` plugin ships with the repo but isn't in index
**Directory:** `docs/models/itambox_esign/` exists with `docusignenvelope.md`

The DocuSign e-sign plugin has doc pages, the plugin system docs use it as the canonical example, and `.env.example` references it. But `docs/index.md` never mentions e-signature as a feature.

---

### 26. No navigation/sidebar structure documented for users
The `development/module-maturity.md` references `MenuGroup` instances and navigation badges, but there's no user-facing guide to the sidebar layout, menu organization, or how to find features in the UI.

---

## Summary by priority

| Priority | Count | Action |
|----------|-------|--------|
| 🔴 HIGH | 4 | Fix index module list, asset field reqs, write usage docs for 16 feature areas, document 40 env vars |
| 🟡 MEDIUM | 9 | Fix API docs (4 missing paths), expand bulk import docs (30+ models), fix field naming errors, document mgmt commands |
| 🟢 LOW | 12 | Fix URL mismatches, add missing model doc pages, fix directory naming, cross-ref accuracy |
