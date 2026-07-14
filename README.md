<p align="center">
  <img src="https://raw.githubusercontent.com/tabler/tabler/master/src/assets/brand/tabler-logo.svg" alt="ITAMbox Logo" width="100" height="100">
</p>

<h1 align="center">ITAMbox</h1>

<p align="center">
  <strong>IT Asset Management (ITAM) platform built on Django, Tabler, and HTMX</strong>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg" alt="Python"></a>
  <a href="https://www.djangoproject.com/"><img src="https://img.shields.io/badge/django-5.2-green.svg" alt="Django"></a>
  <a href="https://htmx.org/"><img src="https://img.shields.io/badge/frontend-HTMX-orange.svg" alt="HTMX"></a>
  <a href="https://tabler.io/"><img src="https://img.shields.io/badge/styling-Tabler%20CSS-blueviolet.svg" alt="Tabler CSS"></a>
  <a href="https://github.com/netbox-community/netbox"><img src="https://img.shields.io/badge/inspired%20by-NetBox-blue.svg" alt="Inspired by NetBox"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/license-Apache%202.0-red.svg" alt="License"></a>
</p>

ITAMbox is an IT asset management (ITAM) and tracking application. Inspired by the strict data modeling approach of **NetBox**, it is designed as a lightweight, customizable inventory tool for hardware, software licenses, maintenance history, and asset financials.

---

## Key Features

*   **Dynamic Custom Fieldsets:** Add custom metadata fields (text, number, date, boolean, dropdowns) to specific `AssetTypes` on the fly. Data is stored in a `JSONField` on the `Asset` model, removing the need for database schema migrations.
*   **Maintenance & TCO Ledger:** Logs support events, upgrades, and repair costs. Calculates asset downtime automatically and aggregates initial cost with maintenance records to compute a true Total Cost of Ownership (TCO).
*   **Symmetric Encryption at Rest:** Protects software product keys and credentials in the database using standard `AES-256 Fernet` cryptography (keys derived from Django's `SECRET_KEY`). Includes a transparent fallback header (`enc$`) for backward-compatibility with plaintext values.
*   **Valuation & Straight-Line Depreciation:** Computes real-time book value using purchase cost, customized lifespans, and salvage values, complete with visual progress metrics.
*   **Onboarding Kits:** Group multiple hardware types, accessories, and software seats into pre-defined kits. Checkout runs inside an atomic transaction block, rolling back entirely if any kit item is out of stock.
*   **Responsive HTMX-based UI:** Page navigation, search filters, and active tab lists update instantly without full page reloads, using simple HTML-swaps and Out-of-Band (OOB) triggers.

---

## Tech Stack

*   **Backend:** Django 5.2, Python 3.11+, Django REST Framework (DRF)
*   **Frontend:** Tabler CSS (Bootstrap 5), HTMX, django-htmx, django-template-partials
*   **Database:** PostgreSQL 15+ (required for all environments)
*   **Core Libraries:** django-tables2 (interactive grids), django-filter (filtering panels), django-crispy-forms (crispy form renderers), cryptography (symmetric AES-256)

---

## System Architecture

### Entity Relationship Diagram

The full, generated relationship graph is maintained in the
[data-model documentation](itambox/docs/development/data-model.md). It groups
all concrete domain models by Django app and labels their direct ORM
relationships.

### HTMX Navigation

ITAMbox uses a dual-template layout to achieve a fast interface without a complex JavaScript frontend framework:
1.  **Full Request:** Renders the outer shell (`base.html`) containing the sidebar, top navigation, and dependencies.
2.  **HTMX Request:** Dynamically swaps out the `#page-content-wrapper` block using a partial template (`base_htmx.html`), updating the active breadcrumbs, actions, tables, and tabs in a single roundtrip.
3.  **Out-of-Band (OOB) Swaps:** Modifies peripheral elements like `<title>` tags and toast notifications on demand.

---

## Getting Started

### Evaluate with Docker Compose (demo data)

To spin up the PostgreSQL database and application server with a full demo
dataset (a managed-service provider, customer tenants, assets, licenses, …):

```bash
# Clone the repository
git clone https://github.com/itambox-itam/itambox-webapp.git
cd itambox-webapp

# Optional: pin the admin password up front (otherwise a strong one is
# generated and printed ONCE by the seed step below — capture it!)
export DJANGO_SUPERUSER_PASSWORD=change-me

# Build and start services
docker compose up -d --build

# Run database migrations
docker compose exec app python manage.py migrate

# Seed demo data (safe on a fresh database; re-seeding an existing
# non-debug database clears domain data and therefore requires --force)
docker compose exec app python manage.py seed_data
```

The app is now live at `http://localhost:8000`. Sign in as `admin` with the
password printed by the seed step (or your `DJANGO_SUPERUSER_PASSWORD`), or
explore the demo roles — e.g. MSP engineer `lars.eklund`, password
`itambox2026`.

### Production first run

Production installs do **not** use demo data. After configuring `.env`
(`ITAMBOX_SECRET_KEY`, `ITAMBOX_FIELD_ENCRYPTION_KEYS`,
`ITAMBOX_CACHE_BACKEND=redis` for multi-worker deployments — see
`.env.example`):

```bash
docker compose up -d --build
docker compose exec app python manage.py migrate
docker compose exec app python manage.py createsuperuser
```

### Local Virtualenv Setup

1. **Set up PostgreSQL**: Ensure a PostgreSQL 15+ server is running locally (e.g., via Docker or system service).
2. **Configure Environment**: Copy `.env.example` to `.env` and update the database connection variables (`ITAMBOX_DB_HOST`, `ITAMBOX_DB_PORT`, etc.).

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations and seed data
cd itambox
python manage.py migrate
python manage.py seed_data

# Start local server in debug mode
ITAMBOX_DEBUG=true python manage.py runserver

# App is now live at http://127.0.0.1:8000
```

---

## Running Tests

Automated unit and integration tests cover model signals, validation constraints, FilterSets, and CRUD APIs. The suite uses `pytest` (pytest-django) and runs from the `itambox/` directory. Tests require a running PostgreSQL instance on port `5433` (the project uses a disposable Postgres container for local testing; SQLite is rejected by settings):

```bash
cd itambox

# Run all tests
pytest

# Test specific applications
pytest assets/tests/
pytest subscriptions/tests/
pytest core/tests/
```

---

## License

This project is licensed under the Apache License 2.0.
