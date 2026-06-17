# Installation

This guide covers the recommended Docker Compose installation path. A bare-metal install (virtualenv + system Postgres) follows the same steps without the `docker compose` wrappers.

## Prerequisites

| Requirement | Minimum |
|---|---|
| Docker | 24+ with Compose v2 |
| Disk | 2 GB free (app + DB volumes) |
| RAM | 1 GB (2 GB recommended for the worker) |

## 1. Clone and configure

```bash
git clone https://github.com/your-org/itambox.git
cd itambox
cp .env.example .env
```

Edit `.env` and fill in every value marked **REQUIRED**. See the full variable reference below.

## 2. Environment variables

All settings are loaded from a `.env` file at the repo root (or one directory above). The loader is a hand-rolled parser — no `python-dotenv` required.

| Variable | Purpose | Default / Notes |
|---|---|---|
| `ITAMBOX_ENV` | Runtime mode: `dev` or `prod` | Fails closed to `prod` when unset |
| `ITAMBOX_SECRET_KEY` | **REQUIRED in prod.** Django secret key — keep it secret, never commit it. | Insecure dev default if unset (emits warning) |
| `ITAMBOX_DB_NAME` | PostgreSQL database name | `itambox` |
| `ITAMBOX_DB_USER` | PostgreSQL user | `itambox` |
| `ITAMBOX_DB_PASSWORD` | **REQUIRED in prod.** PostgreSQL password | — |
| `ITAMBOX_DB_HOST` | PostgreSQL host | `localhost` |
| `ITAMBOX_DB_PORT` | PostgreSQL port | `5432` |
| `ITAMBOX_CACHE_BACKEND` | Cache backend: `locmem` or `redis` (Redis wire protocol; run **Valkey**, the BSD-licensed fork) | `locmem` |
| `ITAMBOX_REDIS_URL` | Valkey/Redis connection string (`redis://` protocol; used when cache=redis) | `redis://127.0.0.1:6379/1` |
| `RATELIMIT_CACHE` | Django cache alias used for rate limiting | `default` |
| `ITAMBOX_TENANT_LDAP_CONFIGS` | JSON object of per-tenant LDAP configs (advanced) | `{}` |
| `ITAMBOX_TENANT_SAML_CONFIGS` | JSON object of per-tenant SAML configs (advanced) | `{}` |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | JSON object of per-tenant OIDC configs (advanced) | `{}` |

!!! warning "Secret key critical"
    ITAMbox encrypts sensitive values (e.g. SMTP passwords) using a Fernet key derived from `ITAMBOX_SECRET_KEY`. **Losing or rotating the secret key will permanently brick all encrypted data.** Back up `.env` alongside your database dump — they are a unit.

## 3. Start services

```bash
docker compose up -d
```

## 4. Run migrations

```bash
docker compose exec app python manage.py migrate
```

!!! warning "PostgreSQL `btree_gist` extension required"
    One migration (`assets.0051`) creates an exclusion constraint that prevents
    overlapping asset reservations, which needs the `btree_gist` extension. The
    migration runs `CREATE EXTENSION btree_gist`, a **non-trusted** extension that
    requires a database **superuser** (or a role with `CREATE` on the database).
    The bundled `docker compose` Postgres role is a superuser, so the default
    path just works. On **managed/external Postgres** (RDS, Cloud SQL, Azure)
    with a least-privilege migration role, pre-create the extension once as an
    admin **before** running `migrate`, otherwise migration aborts and the
    overlap guard is not installed:

    ```sql
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    ```

## 5. Create a superuser

```bash
docker compose exec app python manage.py createsuperuser
```

## 6. (Optional) Load demo data

```bash
docker compose exec app python manage.py seed_data
```

This loads a coherent MSP demo dataset — useful for evaluating the product before entering real data.

## 7. Access the app

Open `http://localhost:8000` (or the port mapped in `docker-compose.yml`). Log in with the superuser credentials you created above.

## Static files (production)

In production, collect static files and serve them from a web server or CDN:

```bash
docker compose exec app python manage.py collectstatic --no-input
```

Configure `STATIC_ROOT` and a suitable web server (Nginx, Caddy) to serve the `static/` directory.
