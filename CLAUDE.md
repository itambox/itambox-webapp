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

### Standard app layout

Domain apps follow a consistent internal layout. Large apps split `models`, `forms`, and `views` into packages; smaller apps keep them as single modules — both are fine, match the neighbouring app rather than imposing a structure.

```text
<app>/
  models.py | models/    # ORM models (package when large — only assets/ splits today)
  forms.py  | forms/     # ModelForms + filter forms; CSV import forms in forms/import_forms.py
  views.py  | views/     # UI views subclassing itambox.views.generic.*
  tables.py              # django-tables2 table classes
  filters.py             # django-filter FilterSet classes (this repo uses filters.py, NOT filtersets.py)
  api/                   # serializers.py / views.py / urls.py — bases imported from itambox.api.*
  schema.py              # GraphQL (graphene) Query/Mutation — GraphQL-exposed apps only
  search.py              # @register_search SearchIndex classes (global search)
  services.py            # domain/service-layer logic — only where a service layer is warranted
  tasks.py               # django-q2 task functions — only where the app enqueues work
  signals.py             # signal receivers — only where needed
  choices.py             # ChoiceSet / enum-style choices
  urls.py                # pk-based routes; app_name set
  admin.py, apps.py
  tests/    | tests.py    # pytest-django tests
```

Not every app has every file: `services.py`, `schema.py`, `tasks.py`, `signals.py`, and `search.py` exist only where the feature is used. `api/` is always a package.

## Tech stack

Django + PostgreSQL (SQLite is rejected at settings load). Beyond Django itself:

- **REST API** — Django REST Framework + drf-spectacular (OpenAPI schema/sidecar).
- **GraphQL** — graphene-django (see "Architecture: GraphQL").
- **Lists & filtering** — django-tables2 (tables) + django-filter (FilterSets).
- **Forms** — django-crispy-forms + crispy-bootstrap5.
- **Frontend interactivity** — HTMX (django-htmx); TypeScript + SCSS compiled via npm into `static/dist/`.
- **Background jobs** — django-q2 (NOT Celery); worker via `manage.py qcluster`.
- **Cache / queue broker** — Valkey/Redis in prod, locmem in dev.
- **Tests** — pytest-django + model_bakery. pytest-xdist is installed but NOT enabled: the suite isn't xdist-safe yet (cross-test shared state — MEDIA_ROOT, requests-mock + django-q timing).
- **Docs** — MkDocs.

Version pins live in `itambox/requirements.txt` and `pyproject.toml` — check there rather than trusting a number quoted here.

## Development commands

Canonical local setup: `pip install -r requirements-dev.txt` from the repository root (layers pytest/flake8/pre-commit/django-debug-toolbar on top of `itambox/requirements.txt`), or `make setup` (see [Makefile](Makefile); requires Git Bash or WSL, plus GNU Make installed separately on Windows). `itambox/requirements.txt` alone remains the runtime-only install.

Native Windows Python 3.12 is a supported development environment — the app and normal tests run there. `itambox/requirements.txt` marks `django-auth-ldap` `platform_system != "Windows"` because its `python-ldap` dependency has no Windows wheel; `core/auth/ldap.py` falls back to a disabled LDAP backend in that case, so LDAP integration/development requires Docker, Linux, or WSL. `python-magic` is likewise excluded via `platform_system != "Windows"` marker (import can hang indefinitely without libmagic DLL); `core/validators.py` falls back to extension checks/Pillow. Docker/Linux/WSL retain full libmagic signature validation. Production remains Docker/Linux with the full dependency set.

All other commands below run from `itambox/`.

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

### Lint (flake8 + flake8-bugbear)
```bash
# From the repository root (not itambox/) -- blocking gate, same command CI and
# pre-commit both run:
python scripts/check_flake8_baseline.py

# After deliberately reducing debt, regenerate with the pinned toolchain on
# canonical Python 3.12. Other interpreter versions are refused:
python scripts/check_flake8_baseline.py --write-baseline
```
Policy (`select`/`ignore`, each ignore documented with a reason) lives in `setup.cfg`
at the repo root. The pinned Flake8/Bugbear toolchain is blocking; the ~4k
pre-existing violations are grandfathered via `scripts/flake8_baseline.json`, a
schema-v3 identity baseline keyed by path, code, message, source statement, and
stable AST context. Its policy SHA-256 binds it to the effective Flake8 config,
targets, and exact tool versions. Canonical Python 3.12 requires exact identity
equality: increases are regressions, while reductions require regenerating the
baseline in the same reviewed cleanup so old headroom cannot hide reintroduced
debt. The gate refuses to run on any interpreter other than canonical Python
3.12; there are no interpreter- or OS-specific exceptions. `make lint` /
`pre-commit run --all-files` use the same managed policy.

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

`CSPMiddleware` (`itambox/middleware.py`) sets the CSP header. Inline scripts are nonce'd per request (`request.csp_nonce`) — `script-src` does not allow `'unsafe-inline'`. Styles still rely on `'unsafe-inline'` in `style-src`: the 164 inline `style=` attributes across templates (`git grep -oE 'style=["\']' -- '*.html' | wc -l`) can't carry a nonce, so this is tracked tech-debt pending an inline-style refactor (move inline styles to CSS classes).

## Architecture: GraphQL

GraphQL uses **graphene-django** (not Strawberry). Each exposed app declares `Query`/`Mutation` classes in `<app>/schema.py`; the root schema in `core/schema.py` combines them (currently `assets`, `inventory`, `licenses`, `software`, `subscriptions`) plus any plugin schema (a plugin opts in via a `graphql_schema` attr on its app config). The endpoint is served by `core/views/graphql.py` — a `GraphQLView` subclass wired through `TenantMiddleware`/`CurrentUserMiddleware` and token auth, with **query-complexity guards**: a depth limit plus a field/alias-count validator (`field_count_limit_validator`) that stops alias-amplification DoS (`a1: assets(...) a2: assets(...) …`). To expose a new app: add `<app>/schema.py` with `Query`/`Mutation`, then add those to the bases in `core/schema.py`. Coverage is tested by `test_graphql.py`, `test_graphql_adversarial.py`, and `test_sec_graphql.py`.

## Architecture: background tasks

Tasks live in `core/tasks/`. Each task function should be wrapped in `TaskContext(tenant_id=..., user_id=...)` to wire up tenant scoping and change-log attribution. Tasks are enqueued with django-q2's `async_task()`, dispatched via `transaction.on_commit()` to avoid running before the triggering transaction commits.

## Common tasks: add a model end-to-end

A fully-wired model touches every layer below. Skipping one leaves a half-wired feature (a model with no API, a list view with no filter, an object missing from search). Mirror an existing model in the same app rather than inventing structure.

1. **Model** — add to `<app>/models.py` (or `models/`). Inherit `BaseModel`; add `ChangeLoggingMixin` for audit history and `AutoSlugMixin` if it needs a slug. Tenant-scoped models get the tenant FK + the appropriate manager (see "Architecture: tenant scoping"); soft-delete uniqueness uses `UniqueConstraint(condition=Q(deleted_at__isnull=True))`, never `unique=True`.
2. **Migration** — `python manage.py makemigrations <app>` (never hand-write).
3. **Filtering** — add a `FilterSet` to `<app>/filters.py` and a filter form to `<app>/forms/` (the list view wires them as `filterset` / `filterset_form`).
4. **Form** — add a crispy `ModelForm` to `<app>/forms/`; for CSV import add a form to `<app>/forms/import_forms.py` decorated with `@register_import_form` (auto-wires to the centralized import view).
5. **Table** — add a `django_tables2` table to `<app>/tables.py`.
6. **REST API** — serializer in `<app>/api/serializers.py`, viewset in `<app>/api/views.py`, route in `<app>/api/urls.py` (bases from `itambox.api.*`); then regenerate the schema with `python manage.py spectacular --file schema.yaml`.
7. **UI views** — subclass the generics in `<app>/views.py` (or `views/`): `ObjectListView` / `ObjectDetailView` / `ObjectEditView` / `ObjectDeleteView` / `ObjectCloneView` / `ObjectBulkEditView` / `ObjectBulkDeleteView`; set `queryset`, `filterset`, `filterset_form`, `table`, and detail `Panel`s.
8. **URLs** — add the pk-based route set to `<app>/urls.py` (`list` / `add` / `<pk>/` / `<pk>/edit/` / `<pk>/clone/` / `<pk>/delete/` + bulk + any custom actions).
9. **Search** — register a `SearchIndex` in `<app>/search.py` with `@register_search()` if the model should appear in global search.
10. **GraphQL** (optional) — expose via `<app>/schema.py` and wire into `core/schema.py` (see "Architecture: GraphQL").
11. **Navigation** — wire the list view into the sidebar navigation so the model is reachable in the UI.
12. **Tests** — add to `<app>/tests/` using `TenantTestMixin`; mirror the existing `test_api.py` / `test_filter_forms.py` / `test_views.py` coverage.

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
