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
  extras/         # Tags, custom fields, config contexts, dashboards, webhooks, event rules
  users/          # User model, preferences, SCIM provisioning
  static/src/     # TypeScript + SCSS source
  static/dist/    # Compiled frontend (git-ignored, rebuild with npm)
```

## Development commands

All commands run from `itambox/`.

### Django
```bash
# Env selection: ITAMBOX_ENV=dev|prod (defaults to dev when ITAMBOX_DEBUG=true)
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

## Architecture: tenant scoping

Every request is scoped to one active tenant, stored in a `contextvars.ContextVar` (`core/managers.py`). **`TenantMiddleware`** resolves the tenant from session/query-param and sets it via `set_current_tenant()` / `set_current_membership()`. This propagates automatically through ORM queries.

Manager hierarchy for tenant-aware models:
- `SoftDeleteManager` — default manager; filters `deleted_at__isnull=True`
- `TenantScopingManager` — also filters to the current tenant's objects
- `AllObjectsManager` — unfiltered; use only for admin/recycle-bin operations
- `TenantScopingSoftDeleteManager` — combines both

Soft-delete models must use `UniqueConstraint(..., condition=Q(deleted_at__isnull=True))` rather than `unique=True` on name/slug fields (active rows only must be unique).

## Architecture: change logging

`ChangeLoggingMixin` (`core/models.py`) records an `ObjectChange` on every `save()` and `delete()`. It relies on two contextvars from `itambox/middleware.py`:
- `_request_id` — a UUID set per HTTP request by `CurrentUserMiddleware`
- `_current_user` — the authenticated user

**If either is `None`, the save is not logged.** Background tasks must use `TaskContext` (`core/tasks/context.py`) as a context manager, which sets both variables for the lifetime of the task, ensuring changes are attributed to the task's user rather than silently skipped.

Call `obj.snapshot()` before making changes to capture the pre-change state; otherwise the mixin re-fetches the row from the DB.

## Architecture: generic views

`itambox/views/generic/__init__.py` provides the reusable view base classes:
- `ObjectListView` — paginated, filterable, tenant-scoped, HTMX-aware list
- `ObjectDetailView` — detail with layout panels
- `ObjectEditView` / `ObjectDeleteView` / `ObjectCloneView`
- `ObjectBulkEditView` / `ObjectBulkDeleteView` / `ObjectImportView`

**HTMX pattern:** `BaseHTMXView` detects boosted (`hx-boost`) vs. partial requests and returns the appropriate template (`content_partial_name` for HTMX, full template otherwise). Service/action views return `204 + HX-Trigger` JSON payload on success (`closeModalEvent`, `tableRefreshRequired`, `showMessage`).

Layout panels are declared as a tuple of `Panel(slot, title)` objects on the view; the `{% render_panel %}` template tag renders them.

## Architecture: permissions & auth

Permissions flow: `TenantMembershipBackend` (`core/auth/__init__.py`) is the first backend. It resolves permissions from a JSON `permissions` field on `TenantRole`; it handles the `obj=` argument by extracting `obj.tenant` and checking the user's membership in that tenant. `PasswordLoginOnlyBackend` blocks all perm checks for password-auth, ensuring all authorization goes through the membership backend.

`StrictTenantPermission` (DRF) enforces object-level tenant boundary on all API detail endpoints via `DEFAULT_PERMISSION_CLASSES`.

**`core.api`** is a compatibility shim package — all its files are single-line `from itambox.api.X import *`. The canonical implementation lives in `itambox/api/`. App-level API code imports from `core.api`; the DRF settings reference `itambox.api`.

## Architecture: background tasks

Tasks live in `core/tasks/`. Each task function should be wrapped in `TaskContext(tenant_id=..., user_id=...)` to wire up tenant scoping and change-log attribution. Tasks are enqueued with django-q2's `async_task()`, dispatched via `transaction.on_commit()` to avoid running before the triggering transaction commits.

## Settings

| Env var | Purpose | Default |
|---|---|---|
| `ITAMBOX_ENV` | `dev` or `prod` | auto-detected from `ITAMBOX_DEBUG` |
| `ITAMBOX_SECRET_KEY` | Django secret key | insecure default (dev only) |
| `ITAMBOX_DB_*` | DB connection | `itambox`/`localhost`/`5432` |
| `ITAMBOX_CACHE_BACKEND` | `locmem` or `redis` | `locmem` |
| `ITAMBOX_REDIS_URL` | Redis connection when cache=redis | `redis://127.0.0.1:6379/1` |
| `RATELIMIT_CACHE` | Cache alias for rate limiting | `default` |
| `ITAMBOX_TENANT_LDAP_CONFIGS` | JSON per-tenant LDAP configs | `{}` |
| `ITAMBOX_TENANT_SAML_CONFIGS` | JSON per-tenant SAML configs | `{}` |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | JSON per-tenant OIDC configs | `{}` |

Reads `.env` from `BASE_DIR` or `BASE_DIR/../` at startup (hand-rolled parser; no `python-dotenv`).

## Testing conventions

- Use `TenantTestMixin` (`core/tests/mixins.py`) for any test that touches tenant-scoped models. It provides `setup_tenant_context()`, `set_active_tenant()`, and a `tenant_context()` context manager.
- `model_bakery` recipes are in `core/tests/baker_recipes.py`.
- Tenant/user contextvars are cleared automatically after each test by `conftest.py`.
- Security boundary tests live in `core/tests/test_tenant_security.py` and `test_security_boundaries.py` — run these when touching auth, middleware, or manager code.

## Plugin system

Plugins are Django apps listed in `settings.PLUGINS`. Each plugin's `__init__.py` must expose a `config` object that subclasses `itambox.plugins.PluginConfig`. `load_plugins()` (called at settings load) merges the plugin's `INSTALLED_APPS`, `MIDDLEWARE`, and config defaults, then registers it with the global `registry`. Plugin API routes mount under `/api/plugins/`; UI routes mount under `/plugins/<base_url>/`.
