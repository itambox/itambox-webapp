# Development Guide — ITAMbox

## Quick Start

1. **Database Requirements**: ITAMbox strictly requires a running PostgreSQL 15+ database. Ensure a server is active locally or accessible.
2. **Environment Variables**: Copy `.env.example` to `.env` and configure the PostgreSQL connection credentials (e.g., `ITAMBOX_DB_ENGINE=django.db.backends.postgresql`, `ITAMBOX_DB_NAME`, `ITAMBOX_DB_USER`, `ITAMBOX_DB_PASSWORD`, `ITAMBOX_DB_HOST`, `ITAMBOX_DB_PORT`).

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run migrations
cd itambox
python manage.py migrate

# Seed sample data (400+ records across all models)
python manage.py seed_data

# Start dev server
ITAMBOX_DEBUG=true python manage.py runserver
```

### Seed Data Modes

| Command | Effect |
|---------|--------|
| `python manage.py seed_data` | Full wipe + reseed |
| `python manage.py seed_data --skip-drop` | Add data without clearing existing |
| `python manage.py seed_data --production` | Minimal seed (admin + status labels only) |

## Tenant FK Pattern

### Adding Tenant to a New Model

1. **Model** — Add `tenant = models.ForeignKey('organization.Tenant', on_delete=models.PROTECT, null=True, blank=True, related_name='<unique_name>', db_index=True)`
2. **Form** — Add `'tenant'` to both `Meta.fields` AND the crispy `Layout` (missing from Layout is the #1 cause of invisible fields)
3. **FilterSet** — Add `tenant = django_filters.ModelChoiceFilter(queryset=Tenant.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}), label='Tenant')`
4. **Table** — Add `tenant = tables.Column(accessor='tenant.name', verbose_name='Tenant', orderable=True)` and add `'tenant'` to `Meta.fields` + `default_columns`
5. **Admin** — Add `'tenant'` to `list_display`, `list_filter`, and `'tenant__name'` to `search_fields`
6. **Serializer** — Add `tenant = NestedTenantSerializer(read_only=True)` + `tenant_id` write-only field

### Shared Models (no tenant needed)

Models that are **catalogs/references** shared across tenants: `Manufacturer`, `Software`, `AssetType`, `ComponentType`, `StatusLabel`, `AssetRole`, `Tag`, `CustomField`, `Depreciation`, `Provider`, `ContactRole`, `Region`, `SiteGroup`, `TenantGroup`

## HTMX Boost Conventions

### Dual-template architecture

- `base.html` — Full page loads (contains sidebar, topbar, footer shell)
- `base_htmx.html` — HTMX-boosted partial responses (swaps only `#page-content-wrapper`)

### OOB swap targets

Always-active DOM elements that persist across navigations:
```html
<span id="page-title-block" style="display:none" aria-hidden="true">
<div id="django-messages" hx-swap-oob="true">
```

### Adding OOB swaps to templates

In `base_htmx.html` or wrapper partials, emit OOB elements BEFORE the main content:
```html
<span id="page-title-block" hx-swap-oob="true">My Page Title</span>
<div id="page-content-wrapper">
    <!-- main content -->
</div>
```

### Filter panel persistence

Filter state uses `localStorage` key `itambox-show-filters`. Call `window.initFiltersToggle()` from `htmx:afterSettle` with `setTimeout(0)` — using `htmx:afterSwap` causes a DOM timing race where class changes are reverted by the rendering pipeline.

### Creating a new HTMX-boosted list page

1. Define `ObjectListView` subclass with `table`, `filterset`, `filterset_form`, `action_buttons`
2. Template auto-resolved: `generic/object_list.html` extends `base_template|default:"base.html"`
3. Pagination: `RequestConfig.configure(table)` is called in `ObjectListView.get_context_data()`
4. Search: extract to `_quick_search.html` partial; use `request.path` not `request.get_full_path`

## Table Performance Patterns

### N+1 Query Prevention

**Pattern 1: `select_related` for FK traversals**
```python
queryset = MyModel.objects.select_related('fk_field', 'fk_field__nested')
```

**Pattern 2: `prefetch_related` for reverse FKs and M2M**
```python
queryset = MyModel.objects.prefetch_related('reverse_set', 'tags')
```

**Pattern 3: `Count` annotation for `.count()` in table columns**
```python
# In view:
queryset = ParentModel.objects.annotate(child_count=Count('children'))
# In table:
child_count = tables.Column(accessor='child_count', verbose_name='Children', orderable=False)
```

**Pattern 4: Pre-built lookup map for GenericForeignKey**
```python
# When a table column renders GFK data, build a single-query map:
assignments = Assignment.objects.filter(object_id__in=pks).select_related('target')
assignee_map = {a.object_id: a.target for a in assignments}
# Inject into objects before table construction:
for obj in objects:
    obj._display = assignee_map.get(obj.pk, '—')
# In table column:
my_column = tables.Column(accessor='_display', ...)
```

**Pattern 5: Custom Queryset methods for annotated counts**
```python
class LicenseQuerySet(models.QuerySet):
    def with_counts(self):
        return self.annotate(assigned_count=Count('assignments'))
```

### FK indexing for PostgreSQL

Always add `db_index=True` to ForeignKey fields. SQLite auto-indexes, but PostgreSQL does not. Missing FK indexes cause table scans on every filtered list view.

## Subscription Module Architecture

### Models

- `Provider` — Vendor/supplier (AWS, Azure, Adobe)
- `Subscription` — Recurring agreement (SaaS, support, maintenance)
- `SubscriptionAssignment` — GenericForeignKey linking subscriptions to Assets, AssetHolders, or Locations

### Computed Properties

- `subscription.is_expired` — `renewal_date < today`
- `subscription.days_until_renewal` — days to next renewal (negative = overdue)
- `subscription.annual_cost` — normalized from billing cycle

### Signals

`subscription_status_check` — Auto-expires subscriptions past renewal date on save.

### API

```
GET    /api/subscriptions/subscriptions/          # List
GET    /api/subscriptions/subscriptions/{id}/     # Detail
PATCH  /api/subscriptions/subscriptions/{id}/status/  # Status-only update
```

## Testing

The suite uses `pytest` (pytest-django). Run all commands from `itambox/`.

> **PostgreSQL is required.** Tests need a running PostgreSQL instance on port
> `5433` — the project uses a disposable Postgres container for local testing.
> SQLite is rejected at settings load, so the suite will not run without it.

```bash
# Run all tests
pytest

# Run specific app
pytest subscriptions/tests/
pytest assets/tests/
pytest core/tests/

# Run with verbose output
pytest -v
```

Tests run under pytest-django (with `model_bakery` for fixtures) and use the Django test `Client` for views. Model tests verify field constraints, properties, and signals. FilterSet tests verify individual filter parameters. View tests cover HTTP 200/302 for all CRUD operations.
