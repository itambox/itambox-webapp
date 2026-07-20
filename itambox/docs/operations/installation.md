# Installation

This guide covers the source-built Docker Compose production stack. For a disposable evaluation with development settings and demo data, use "Evaluate from source" in the repository-root `README.md` instead.

ITAMbox has no published container image or tagged release yet. Pin every deployment to a reviewed commit; do not deploy a moving branch without reviewing its migrations and changelog.

## Prerequisites

- Docker Engine 24 or newer with Docker Compose v2.24.4 or newer
- A DNS name and TLS-capable reverse proxy or ingress
- Durable storage for PostgreSQL and uploaded media
- A secrets manager or encrypted backup location

## 1. Clone and pin the source

```bash
git clone https://github.com/itambox/itambox-webapp.git
cd itambox-webapp
DEPLOY_REVISION='full-reviewed-commit-sha'
git checkout --detach "$DEPLOY_REVISION"
cp .env.example .env
```

## 2. Configure production values

The Compose file forces `ITAMBOX_ENV=prod` for the application and worker. Edit `.env` before building:

| Variable | Production requirement |
|---|---|
| `ITAMBOX_SECRET_KEY` | Long random Django secret; production refuses the development fallback |
| `ITAMBOX_FIELD_ENCRYPTION_KEYS` | Dedicated comma-separated Fernet keyring; the first key encrypts and all listed keys decrypt |
| `ITAMBOX_API_TOKEN_PEPPERS` | JSON object mapping numeric rotation IDs to dedicated secrets of at least 50 characters |
| `ITAMBOX_DB_NAME`, `ITAMBOX_DB_USER`, `ITAMBOX_DB_PASSWORD` | PostgreSQL database credentials |
| `ITAMBOX_ALLOWED_HOSTS` | External host names plus `127.0.0.1` for the container health probe |
| `ITAMBOX_CSRF_TRUSTED_ORIGINS` | Scheme-qualified external HTTPS origins |
| `ITAMBOX_EMAIL_*` | Working SMTP settings for resets, alerts, and reports |

Generate independent values rather than reusing one secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The last value can be placed in a pepper mapping such as `{"1":"<generated-value>"}`. Invalid JSON is ignored and falls back to a `SECRET_KEY`-derived pepper, so verify the syntax before admitting users.

!!! danger "Back up the complete secret set"
    Back up `.env`, especially `ITAMBOX_SECRET_KEY`, `ITAMBOX_FIELD_ENCRYPTION_KEYS`, and `ITAMBOX_API_TOKEN_PEPPERS`, with the database and media. Losing the field-encryption keyring makes encrypted SMTP passwords, license keys, and webhook secrets unreadable. Losing token peppers invalidates existing API tokens.

## 3. Build and initialize

```bash
docker compose build
docker compose run --rm app python manage.py migrate
docker compose run --rm app python manage.py createsuperuser
```

The image builds frontend assets and runs `collectstatic`; WhiteNoise serves those assets from the application image. A separate static-file volume or web-server mapping is not required by the included stack.

Do not run the full `seed_data` command against production. Its demo dataset contains public credentials and, without `--skip-drop`, clears domain data. Use the development evaluation path instead.

!!! warning "PostgreSQL `btree_gist` extension required"
    Migration `assets.0051` creates the `btree_gist` extension for reservation overlap constraints. The bundled PostgreSQL role can create it. For a managed or least-privilege external database, create the extension as an administrator before running migrations:

    ```sql
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    ```

## 4. Terminate TLS at a reverse proxy

Production settings redirect HTTP to HTTPS and set secure-only session and CSRF cookies. Before starting the long-running services, route the external HTTPS host to the app's HTTP upstream on port 8000, preserve the `Host` header, and set `X-Forwarded-Proto: https`. Do not present `http://localhost:8000` as the user-facing URL.

Restrict the published port to the trusted proxy or firewall boundary; `docker-compose.yml` publishes port 8000 but does not provide TLS itself.

## 5. Verify

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 app worker
curl -I https://itam.example.com/health/
```

Replace the example host with the configured external URL, then sign in with the superuser created during initialization.

Next, establish a tested [backup and restore](backup-restore.md) process before loading production data. Follow the [upgrade guide](upgrades.md) for later source revisions.
