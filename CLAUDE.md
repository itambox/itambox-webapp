# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project layout

All Python/Django code lives in `itambox/`. Commands below assume you `cd itambox` first (or prefix paths accordingly). `manage.py` is at `itambox/manage.py`.

```
itambox/          # Django project root
  core/           # Framework layer: models (BaseModel, ChangeLoggingMixin), managers,
  |               #   mixins, auth backends, settings, background task wrappers
  itambox/        # Generic infrastructure: generic views, middleware, API base, plugins,
  |               #   registry, panels, HTMX helpers, URL root
  assets/         # Hardware asset tracking (core domain)
  inventory/      # Accessories, consumables, components + stock management
  organization/   # Tenants, tenant groups, contacts, locations, AssetHolder
  compliance/     # Custody receipts, audit campaigns
  procurement/    # Purchase orders, requisitions
  subscriptions/  # SaaS subscription tracking
  licenses/       # Software license seat management
  software/       # Installed software catalogue
  extras/         # Tags, custom fields, config contexts, dashboards, journal entries,
  |               #   attachments, reporting, alerting, webhooks, event rules
  users/          # User model, preferences, SCIM provisioning
  static/src/     # TypeScript + SCSS source
  static/dist/    # Compiled frontend (git-ignored, rebuild with npm)
```

## Development commands

All commands run from `itambox/`.

### Django
```bash
# Env selection: ITAMBOX_ENV=dev|prod. Fails closed to prod when neither
# ITAMBOX_ENV nor ITAMBOX_DEBUG is set (test runs default to dev).
DJANGO_SETTINGS_MODULE=core.settings.dev python manage.py runserver

python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

### Tests (pytest-django)
```bash
# Run all tests
pytest

# Run a single test file
pytest assets/tests/test_assignments.py

# Run a single test
pytest assets/tests/test_assignments.py::TestAssetAssignment::test_active_assignment

# Run with coverage
pytest --cov=. --cov-report=html

# Adversarial GraphQL suite (uses a separate test DB to avoid collisions)
pytest assets/tests/test_graphql_adversarial.py
```

Tests require a running PostgreSQL instance. SQLite is explicitly rejected by settings. The `conftest.py` at `itambox/` clears tenant/user contextvars after each test via an `autouse` fixture.

### Frontend
```bash
# Install dependencies once
npm install

# Full build (SCSS + vendor copy + JS bundle)
npm run build:all

# JS bundle only
npm run build

# Watch mode during development
npm run watch
npm run watch:css   # CSS only
```

### Background worker
```bash
python manage.py qcluster   # Start django-q2 worker
```

In tests, `Q_CLUSTER['sync'] = True` is set automatically so tasks run inline.

### Docs (MkDocs)
```bash
# Build docs to static/docs/ (run from itambox/)
mkdocs build --strict

# Live-reload preview
mkdocs serve
```

### API schema
```bash
# Regenerate schema.yaml after model/serializer changes
python manage.py spectacular --file schema.yaml
```

## Architecture: tenant scoping

Every request is scoped to one active tenant, stored in a `contextvars.ContextVar` (`core/managers.py`). **`TenantMiddleware`** resolves the tenant from session/query-param and sets it via `set_current_tenant()` / `set_current_membership()`. This propagates automatically through ORM queries.

Manager hierarchy for tenant-aware models:
- `SoftDeleteManager` â€” default manager; filters `deleted_at__isnull=True`
- `TenantScopingManager` â€” also filters to the current tenant's objects
- `AllObjectsManager` â€” unfiltered; use only for admin/recycle-bin operations
- `TenantScopingSoftDeleteManager` â€” combines both

Soft-delete models must use `UniqueConstraint(..., condition=Q(deleted_at__isnull=True))` rather than `unique=True` on name/slug fields (active rows only must be unique).

## Architecture: change logging

`ChangeLoggingMixin` (`core/models.py`) records an `ObjectChange` on every `save()` and `delete()`. It relies on two contextvars from `itambox/middleware.py`:
- `_request_id` â€” a UUID set per HTTP request by `CurrentUserMiddleware`
- `_current_user` â€” the authenticated user

**If either is `None`, the save is not logged.** Background tasks must use `TaskContext` (`core/tasks/context.py`) as a context manager, which sets both variables for the lifetime of the task, ensuring changes are attributed to the task's user rather than silently skipped.

Call `obj.snapshot()` before making changes to capture the pre-change state; otherwise the mixin re-fetches the row from the DB.

## Architecture: generic views

`itambox/views/generic/__init__.py` provides the reusable view base classes:
- `ObjectListView` â€” paginated, filterable, tenant-scoped, HTMX-aware list
- `ObjectDetailView` â€” detail with layout panels
- `ObjectEditView` / `ObjectDeleteView` / `ObjectCloneView`
- `ObjectBulkEditView` / `ObjectBulkDeleteView` / `ObjectImportView`

**HTMX pattern:** `BaseHTMXView` detects boosted (`hx-boost`) vs. partial requests and returns the appropriate template (`content_partial_name` for HTMX, full template otherwise). Service/action views return `204 + HX-Trigger` JSON payload on success (`closeModalEvent`, `tableRefreshRequired`, `showMessage`).

Layout panels are declared as a tuple of `Panel(slot, title)` objects on the view; the `{% render_panel %}` template tag renders them.

**URL routing convention:** URLs are pk-based; slugs are stable natural keys for import/export and filtering â€” never routing. `AutoSlugMixin` populates the slug on save; `ObjectDetailView`/`ObjectEditView` resolve edit/delete/clone URLs exclusively via `kwargs={'pk': ...}`.

### Inline-import policy

Imports live at module top. A function-local (inline) import is justified ONLY to (a) avoid `AppRegistryNotReady` at import time, (b) break a real circular import, or (c) defer an optional/heavy dependency. Every other inline import — plain stdlib, plain Django, and local-app imports with no cycle — must be hoisted. When an inline import is genuinely required, annotate it with a one-line reason, e.g. `# inline import: breaks <A> <-> <B> circular import`.

## Architecture: permissions & auth

Permissions flow: `TenantMembershipBackend` (`core/auth/__init__.py`) is the first backend. It resolves permissions from a JSON `permissions` field on `TenantRole`; it handles the `obj=` argument by extracting `obj.tenant` and checking the user's membership in that tenant. `PasswordLoginOnlyBackend` blocks all perm checks for password-auth, ensuring all authorization goes through the membership backend.

`StrictTenantPermission` (DRF) enforces object-level tenant boundary on all API detail endpoints via `DEFAULT_PERMISSION_CLASSES`.

The canonical API implementation lives in `itambox/api/`. All app-level API code (`serializers.py`, `views.py`) imports directly from `itambox.api.*`.

### Content Security Policy

`CSPMiddleware` (`itambox/middleware.py`) sets the CSP header. Inline scripts are nonce'd per request (`request.csp_nonce`) — `script-src` does not allow `'unsafe-inline'`. Styles still rely on `'unsafe-inline'` in `style-src`: the ~675 inline `style=` attributes across templates can't carry a nonce, so this is tracked tech-debt pending an inline-style refactor (move inline styles to CSS classes).

## Architecture: background tasks

Tasks live in `core/tasks/`. Each task function should be wrapped in `TaskContext(tenant_id=..., user_id=...)` to wire up tenant scoping and change-log attribution. Tasks are enqueued with django-q2's `async_task()`, dispatched via `transaction.on_commit()` to avoid running before the triggering transaction commits.

## Settings

| Env var | Purpose | Default |
|---|---|---|
| `ITAMBOX_ENV` | `dev` or `prod` | fail-closed to `prod` when unset (dev under tests) |
| `ITAMBOX_SECRET_KEY` | Django secret key | insecure default (dev only) |
| `ITAMBOX_FIELD_ENCRYPTION_KEYS` | Comma-separated Fernet keys for field encryption (`License.product_key`, SMTP password, webhook secret); first key encrypts, all keys decrypt (rotation). **Unset derives the key from `SECRET_KEY` — insecure: rotating `SECRET_KEY` then makes encrypted fields unrecoverable.** Set a stable value in prod and back it up. | unset (derives from `SECRET_KEY`) |
| `ITAMBOX_BASE_URL` | Public base URL for QR labels & outbound links (no trailing slash) | `""` (bare-tag QR used) |
| `ITAMBOX_DEFAULT_CURRENCY` | ISO 4217 fallback for money display; `{{ value|money:obj }}` resolves tenant currency first | `EUR` |
| `ITAMBOX_DB_*` | DB connection | `itambox`/`localhost`/`5432` |
| `ITAMBOX_CACHE_BACKEND` | `locmem` or `redis` (Redis wire protocol — run Valkey, the BSD-licensed fork) | `locmem` |
| `ITAMBOX_REDIS_URL` | Valkey/Redis connection (`redis://` protocol) when cache=redis | `redis://127.0.0.1:6379/1` |
| `RATELIMIT_CACHE` | Cache alias for rate limiting. Under multi-worker prod this (and the SAML replay-protection cache, which uses the `default` alias) MUST resolve to a shared redis/Valkey backend — a per-process `locmem` cache makes login counters per-worker (effective limit × workers) and weakens SAML replay protection to per-process. See "Caching in production". | `default` |
| `ITAMBOX_TENANT_LDAP_CONFIGS` | JSON per-tenant LDAP configs | `{}` |
| `ITAMBOX_TENANT_SAML_CONFIGS` | JSON per-tenant SAML configs | `{}` |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | JSON per-tenant OIDC configs | `{}` |
| `ITAMBOX_TENANT_INTUNE_CONFIGS` | JSON per-tenant Intune discovery configs | `{}` |
| `ITAMBOX_DOCS_ROOT` | Filesystem path to compiled MkDocs output | `BASE_DIR/docs` |
| `ITAMBOX_REQUIRE_MFA` | Enforce TOTP MFA (django-otp) for local-password logins by superusers/owner-admin roles. SSO/LDAP/SAML/OIDC delegate MFA to the IdP and are always exempt. | `False` (dev); `True` (prod) |
| `ITAMBOX_REQUIRE_CUSTODY_SIGNIN` | Require digital signature on custody receipt sign-off | `True` |
| `ITAMBOX_ALLOW_GLOBAL_CUSTODY_TEMPLATES` | Allow custody templates not scoped to a tenant | `True` |
| `ITAMBOX_SERVER_EMAIL` | From-address for error emails (prod only) | `DEFAULT_FROM_EMAIL` |
| `ITAMBOX_ENABLE_EXTENDED_ORG_HIERARCHY` | Show Regions & Site Groups in sidebar nav | `False` |
| `ITAMBOX_PAGINATOR_COUNT_CAP` | Upper bound for the list-page row counter (`EnhancedPaginator`). A plain `SELECT COUNT(*)` scans the whole filtered table on every list view (slow at NetBox scale); the paginator counts only up to this many rows. At or below the cap the total is exact (small tables and tests are unaffected); above it the UI shows "<cap>+". Set `0` to disable capping (stock unbounded count). | `100000` |

Reads `.env` from `BASE_DIR` or `BASE_DIR/../` at startup (hand-rolled parser; no `python-dotenv`).

### Caching in production

Rate limiting (`RATELIMIT_CACHE`) and SAML replay protection both read through the Django cache. Under multi-worker gunicorn a per-process `locmem` cache silently breaks them: login/throttle counters become per-worker (so the effective limit is `RATELIMIT_LIMIT × workers`) and SAML assertion replay protection only dedupes within a single worker. Set `ITAMBOX_CACHE_BACKEND=redis` (+ `ITAMBOX_REDIS_URL`, pointing at Valkey/Redis) so all workers share one counter store. `core/settings/prod.py` logs a loud warning at startup when `CACHE_BACKEND=locmem` in production.

## Testing conventions

- Use `TenantTestMixin` (`core/tests/mixins.py`) for any test that touches tenant-scoped models. It provides `setup_tenant_context()`, `set_active_tenant()`, and a `tenant_context()` context manager.
- `model_bakery` recipes are in `core/tests/baker_recipes.py`.
- Tenant/user contextvars are cleared automatically after each test by `conftest.py`.
- Security boundary tests live in `core/tests/test_tenant_security.py` and `test_security_boundaries.py` â€” run these when touching auth, middleware, or manager code.

## Plugin system

Plugins are Django apps listed in `settings.PLUGINS`. Each plugin's `__init__.py` must expose a `config` object that subclasses `itambox.plugins.PluginConfig`. `load_plugins()` (called at settings load) merges the plugin's `INSTALLED_APPS`, `MIDDLEWARE`, and config defaults, then registers it with the global `registry`. Plugin API routes mount under `/api/plugins/`; UI routes mount under `/plugins/<base_url>/`.
